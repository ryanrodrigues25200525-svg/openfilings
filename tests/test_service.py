from __future__ import annotations

from collections import Counter

import httpx
import pytest

from openfilings.adapters.base import SourceDocument
from openfilings.adapters.esef import NETHERLANDS, EsefClient
from openfilings.adapters.fca_nsm import FcaNsmClient
from openfilings.exceptions import ConfigurationError
from openfilings.models import Filing
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache


@pytest.mark.asyncio
async def test_fca_pipeline_uses_compressed_content_cache(tmp_path) -> None:
    calls: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        calls[request.url.path] += 1
        if request.method == "POST":
            return httpx.Response(200, json=_nsm_search_response())
        if request.url.path == "/artefacts/NSM/RNS/disclosure-123.html":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html><body><h2>Trading update</h2></body></html>",
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        nsm = FcaNsmClient(client=http_client)
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(cache, nsm_source=nsm)

        companies = await service.search_companies("Tesco")
        filings = await service.list_filings(companies[0].id)
        first = await service.get_filing_markdown(filings[0].id)
        second = await service.get_filing_markdown(filings[0].id)
        cache.close()

    assert first.from_cache is False
    assert second.from_cache is True
    assert first.markdown == second.markdown
    assert companies[0].id == "uk_lei_2138002P5RNKC5W2JZ46"
    assert companies[0].sources == ("fca_nsm",)
    assert {filing.source for filing in filings} == {"fca_nsm"}
    assert "## Trading update" in first.markdown
    assert calls["/artefacts/NSM/RNS/disclosure-123.html"] == 1


@pytest.mark.asyncio
async def test_unknown_source_is_rejected(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    service = OpenFilingsService(cache)

    with pytest.raises(ConfigurationError, match="fca_nsm, edinet, esef"):
        await service.search_companies("Tesco", source="private-registry")  # type: ignore[arg-type]

    cache.close()


@pytest.mark.asyncio
async def test_netherlands_esef_pipeline_searches_lists_and_extracts(tmp_path) -> None:
    lei = "724500Y6DUVHQD6OXN27"
    report_path = f"/{lei}/2025-12-31/ESEF/NL/0/asml-report-en.xhtml"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/entities":
            return httpx.Response(200, json=_esef_entity_response(lei))
        if request.url.path == f"/api/entities/{lei}/filings":
            return httpx.Response(200, json=_esef_filing_response(lei, report_path))
        if request.url.path == report_path:
            return httpx.Response(
                200,
                headers={"content-type": "application/xhtml+xml"},
                content=(
                    b"<html><body><h2>Financial statements</h2>"
                    b"<p>Revenue and operating income.</p></body></html>"
                ),
            )
        raise AssertionError(f"Unexpected ESEF request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        esef = EsefClient(NETHERLANDS, client=http)
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(cache, esef_sources=(esef,))

        company = await service.company("ASML", source="esef")
        filings = await company.get_filings(source="esef")
        filing = filings.latest()
        assert filing is not None
        document = await filing.obj()
        markdown = await filing.markdown()
        search_results = await filing.search("revenue operating")
        resolved = await service.filing(filing.id)
        prefetch = await filings.prefetch()

        offline_service = OpenFilingsService(cache)
        offline_company = await offline_service.company("ASML", offline=True)
        offline_filings = await offline_company.get_filings(offline=True)
        cache.close()

    assert company.id == f"nl_lei_{lei}"
    assert company.name == "ASML Holding N.V."
    assert filing.id == "nl_esef_23718"
    assert "## Financial statements" in document.markdown
    assert document.content.extraction_method == "markdownify"
    assert markdown == document.markdown
    assert search_results[0].section.title == "Financial statements"
    assert resolved.record == filing.record
    assert prefetch.documents_cached == 1
    assert prefetch.failures == ()
    assert offline_company.id == company.id
    assert offline_filings.latest() is not None
    assert offline_filings.latest().id == filing.id


@pytest.mark.asyncio
async def test_image_only_financials_use_configured_ocr(tmp_path, monkeypatch) -> None:
    import pymupdf

    pdf = pymupdf.open()
    pdf.new_page()
    pdf_bytes = pdf.tobytes()
    pdf.close()

    filing = Filing(
        id="sg_sgx_scanned",
        company_id="sg_sgx_1L01",
        source="sgx",
        source_id="scanned",
        title="2025 Annual Report",
        category="accounts",
        filing_type="annual",
        filing_date="2026-03-01",
        period_end="2025-12-31",
        document_id="https://example.test/scanned.pdf",
        media_type="application/pdf",
        issuer_name="Example Limited",
        pdf_available=True,
        source_url="https://example.test/scanned.pdf",
    )
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    cache.put_filings([filing])
    service = OpenFilingsService(
        cache,
        ocr_available=lambda _: True,
        ocr_converter=lambda *_args, **_kwargs: _ocr_financial_statement(),
    )

    async def download(_filing: Filing) -> SourceDocument:
        return SourceDocument(
            data=pdf_bytes,
            media_type="application/pdf",
            source_url="https://example.test/scanned.pdf",
        )

    monkeypatch.setattr(service, "_download_document", download)
    financials = await service.get_filing_financials(filing.id)
    cache.close()

    assert financials.extraction_method == "pdf-ocr-text"
    assert financials.income_statement() is not None


def _ocr_financial_statement() -> str:
    return """
    ## Page 1

    Consolidated income statement
    S$ million
    2025
    2024
    Revenue
    1,234
    1,100
    Operating profit
    300
    250
    Profit before tax
    280
    230
    """


def _nsm_search_response() -> dict[str, object]:
    return {
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [
                {
                    "_id": "disclosure-123",
                    "_source": {
                        "disclosure_id": "disclosure-123",
                        "company": "TESCO PLC",
                        "lei": "2138002P5RNKC5W2JZ46",
                        "related_org": [],
                        "headline": "Trading update",
                        "type": "Trading Statement",
                        "type_code": "TST",
                        "document_date": "2026-07-01",
                        "publication_date": "2026-07-01T07:00:00Z",
                        "download_link": "NSM/RNS/disclosure-123.html",
                        "latest_flag": "Y",
                    },
                }
            ],
        }
    }


def _esef_entity_response(lei: str) -> dict[str, object]:
    return {
        "data": [
            {
                "type": "entity",
                "id": "1969",
                "attributes": {"name": "ASML Holding N.V.", "identifier": lei},
            }
        ]
    }


def _esef_filing_response(lei: str, report_path: str) -> dict[str, object]:
    return {
        "data": [
            {
                "type": "filing",
                "id": "23718",
                "attributes": {
                    "country": "NL",
                    "period_end": "2025-12-31",
                    "date_added": "2026-03-04 13:55:19",
                    "fxo_id": f"{lei}-2025-12-31-ESEF-NL-0",
                    "report_url": report_path,
                    "error_count": 0,
                    "warning_count": 6,
                },
                "relationships": {"entity": {"data": {"type": "entity", "id": "1969"}}},
            }
        ],
        "included": _esef_entity_response(lei)["data"],
    }
