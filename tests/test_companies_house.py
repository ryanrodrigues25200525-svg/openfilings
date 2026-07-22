from __future__ import annotations

import httpx
import pytest

from openfilings.adapters.companies_house import CompaniesHouseClient
from openfilings.exceptions import DocumentUnavailableError, SourceError


@pytest.mark.asyncio
async def test_search_and_list_filings_normalize_source_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        if request.url.path == "/search/companies":
            assert request.url.params["q"] == "Tesco"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "title": "TESCO PLC",
                            "company_number": "00445790",
                            "company_status": "active",
                            "company_type": "plc",
                            "address_snippet": "Tesco House, Shire Park",
                        }
                    ]
                },
            )
        if request.url.path == "/company/00445790/filing-history":
            assert request.url.params["category"] == "accounts"
            return httpx.Response(
                200,
                json={"items": [_filing_payload()], "total_count": 1},
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=httpx.BasicAuth("test-key", ""),
    ) as http_client:
        source = CompaniesHouseClient("test-key", client=http_client)
        companies = await source.search_companies("Tesco")
        filings = await source.list_filings("uk_00445790")

    assert companies[0].id == "uk_00445790"
    assert companies[0].name == "TESCO PLC"
    assert filings[0].company_id == "uk_00445790"
    assert filings[0].document_id == "doc-123"
    assert filings[0].media_type is None
    assert filings[0].has_document is True


@pytest.mark.asyncio
async def test_download_rejects_non_pdf_content() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="no")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = CompaniesHouseClient("test-key", client=http_client)
        with pytest.raises(DocumentUnavailableError, match="text/html"):
            await source.download_pdf("doc-123")


@pytest.mark.asyncio
async def test_download_document_prefers_tagged_xhtml() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/document/doc-123":
            return httpx.Response(
                200,
                json={
                    "resources": {
                        "application/pdf": {"content_length": 2000},
                        "application/xhtml+xml": {"content_length": 1000},
                    }
                },
            )
        if request.url.path == "/document/doc-123/content":
            assert request.headers["accept"] == "application/xhtml+xml"
            return httpx.Response(
                200,
                headers={"content-type": "application/xhtml+xml"},
                content=b"<html><body>Tagged accounts</body></html>",
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = CompaniesHouseClient("test-key", client=client)
        document = await source.download_document("doc-123")

    assert document.media_type == "application/xhtml+xml"
    assert b"Tagged accounts" in document.data


@pytest.mark.asyncio
async def test_source_errors_include_upstream_detail() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "company-profile-not-found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = CompaniesHouseClient("test-key", client=http_client)
        with pytest.raises(SourceError, match="company-profile-not-found"):
            await source.list_filings("00000000")


def _filing_payload() -> dict[str, object]:
    return {
        "transaction_id": "MzAwOTE3NzA0N2FkaXF6a2N4",
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
