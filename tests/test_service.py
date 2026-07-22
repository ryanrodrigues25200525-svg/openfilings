from __future__ import annotations

from collections import Counter

import httpx
import pytest

from openfilings.adapters.companies_house import CompaniesHouseClient
from openfilings.adapters.fca_nsm import FcaNsmClient
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache


@pytest.mark.asyncio
async def test_end_to_end_pipeline_uses_compressed_content_cache(tmp_path) -> None:
    calls: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        calls[request.url.path] += 1
        if request.url.path == "/search/companies":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "title": "TESCO PLC",
                            "company_number": "00445790",
                            "company_status": "active",
                            "company_type": "plc",
                        }
                    ]
                },
            )
        if request.url.path == "/company/00445790/filing-history":
            return httpx.Response(
                200, json={"items": [_filing_payload()], "total_count": 1}
            )
        if request.url.path == "/document/doc-123":
            return httpx.Response(
                200,
                json={"resources": {"application/pdf": {"content_length": 20}}},
            )
        if request.url.path == "/document/doc-123/content":
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-test-fixture",
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = CompaniesHouseClient("test-key", client=http_client)
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(
            source,
            cache,
            converter=lambda _: "## Consolidated income statement\n",
        )

        companies = await service.search_companies("Tesco")
        filings = await service.list_filings(companies[0].id)
        first = await service.get_filing_markdown(filings[0].id)
        second = await service.get_filing_markdown(filings[0].id)
        cache.close()

    assert first.from_cache is False
    assert second.from_cache is True
    assert first.markdown == second.markdown
    assert "# Accounts with accounts type full (AA)" in first.markdown
    assert "## Consolidated income statement" in first.markdown
    assert calls["/document/doc-123/content"] == 1


@pytest.mark.asyncio
async def test_filing_can_be_fetched_without_prior_list_call(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/filing-history/transaction-1"):
            return httpx.Response(200, json=_filing_payload("transaction-1"))
        if request.url.path == "/document/doc-123":
            return httpx.Response(
                200,
                json={"resources": {"application/pdf": {"content_length": 20}}},
            )
        if request.url.path == "/document/doc-123/content":
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-test-fixture",
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = CompaniesHouseClient("test-key", client=http_client)
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(source, cache, converter=lambda _: "Body\n")
        content = await service.get_filing_markdown("uk_00445790_transaction-1")
        cache.close()

    assert content.filing_id == "uk_00445790_transaction-1"


@pytest.mark.asyncio
async def test_unified_search_resolves_lei_and_lists_both_sources(tmp_path) -> None:
    def companies_house_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/companies":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "title": "TESCO PLC",
                            "company_number": "00445790",
                            "company_status": "active",
                            "company_type": "plc",
                        }
                    ]
                },
            )
        if request.url.path == "/company/00445790/filing-history":
            return httpx.Response(
                200, json={"items": [_filing_payload()], "total_count": 1}
            )
        raise AssertionError(f"Unexpected Companies House request: {request.url}")

    def nsm_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=_nsm_search_response())
        if request.url.path == "/artefacts/NSM/RNS/disclosure-123.html":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html><body><h2>Trading update</h2></body></html>",
            )
        raise AssertionError(f"Unexpected NSM request: {request.url}")

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(companies_house_handler)
        ) as ch_http,
        httpx.AsyncClient(transport=httpx.MockTransport(nsm_handler)) as nsm_http,
    ):
        companies_house = CompaniesHouseClient("test-key", client=ch_http)
        nsm = FcaNsmClient(client=nsm_http)
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(
            companies_house,
            cache,
            nsm_source=nsm,
        )

        companies = await service.search_companies("Tesco")
        filings = await service.list_filings(companies[0].id, limit=10)
        nsm_filing = next(item for item in filings if item.source == "fca_nsm")
        content = await service.get_filing_markdown(nsm_filing.id)
        cache.close()

    assert len(companies) == 1
    assert companies[0].id == "uk_00445790"
    assert companies[0].lei == "2138002P5RNKC5W2JZ46"
    assert companies[0].sources == ("companies_house", "fca_nsm")
    assert {filing.source for filing in filings} == {"companies_house", "fca_nsm"}
    assert all(filing.company_id == "uk_00445790" for filing in filings)
    assert "## Trading update" in content.markdown
    assert content.extraction_method == "markdownify"


def _filing_payload(transaction_id: str = "transaction-1") -> dict[str, object]:
    return {
        "transaction_id": transaction_id,
        "category": "accounts",
        "type": "AA",
        "date": "2025-05-12",
        "description": "accounts-with-accounts-type-full",
        "pages": 184,
        "links": {
            "document_metadata": (
                "https://document-api.company-information.service.gov.uk/"
                "document/doc-123"
            )
        },
    }


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
