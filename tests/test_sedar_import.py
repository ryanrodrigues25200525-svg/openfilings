from __future__ import annotations

from datetime import date

import httpx
import pytest

from openfilings.adapters.sedar import SedarClient
from openfilings.exceptions import CompanyNotFoundError, DocumentUnavailableError
from openfilings.models import Company
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache

DOCUMENT_URL = (
    "https://www.sedarplus.ca/csa-party/viewInstance/view.html"
    "?id=0c11f8b7998bcd96984d418144b27b639eabe5bf2127377d"
)


@pytest.mark.asyncio
async def test_local_sedar_pdf_joins_normal_filing_pipeline(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    cache.put_companies([_company()])
    sedar = SedarClient(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: pytest.fail(f"Unexpected request: {request.url}")
            )
        )
    )
    service = OpenFilingsService(
        cache,
        market_sources=(sedar,),
        converter=lambda _: "# Financial statements\n\nRevenue was CAD 1.2 billion.",
    )

    filing = await service.import_sedar_filing(
        _company().id,
        document_url=DOCUMENT_URL,
        document_data=b"%PDF-1.7 user-selected filing",
        title="2025 Annual Report",
        filing_date=date(2026, 3, 12),
        period_end=date(2025, 12, 31),
    )
    filings = await service.list_filings(_company().id, source="sedar")
    first = await filing.content()
    second = await service.get_filing_markdown(filing.id)

    assert filings == [filing.record]
    assert filing.id.startswith("ca_sedar_filing_")
    assert filing.record.pdf_available is True
    assert first.from_cache is False
    assert second.from_cache is True
    assert "Revenue was CAD 1.2 billion." in first.markdown
    assert cache.stats().source_documents == 1
    assert cache.stats().compressed_source_bytes > 0
    await sedar.aclose()
    cache.close()


@pytest.mark.asyncio
async def test_generated_url_download_is_allowlisted_and_cached(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DOCUMENT_URL
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.7 official generated link",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sedar = SedarClient(client=http)
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        cache.put_companies([_company()])
        service = OpenFilingsService(cache, market_sources=(sedar,))
        filing = await service.import_sedar_filing(
            _company().id,
            document_url=DOCUMENT_URL,
            title="Q1 2026 Interim Financial Statements",
            filing_date=date(2026, 5, 8),
            period_end=date(2026, 3, 31),
            filing_type="interim",
        )

    cached = cache.get_source_document(filing.id)
    cache.close()

    assert cached is not None
    assert cached.data.startswith(b"%PDF")
    assert cached.profile == "sedar-import"


@pytest.mark.asyncio
async def test_generated_url_rejects_host_escape_and_browser_page() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                302,
                headers={"location": "https://example.com/private.pdf"},
            )
        )
    ) as http:
        sedar = SedarClient(client=http)
        with pytest.raises(DocumentUnavailableError, match="SEDAR\\+ generated"):
            await sedar.download_document(DOCUMENT_URL)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html><title>Radware verification</title></html>",
            )
        )
    ) as http:
        sedar = SedarClient(client=http)
        with pytest.raises(DocumentUnavailableError, match="local PDF"):
            await sedar.download_document(DOCUMENT_URL)


@pytest.mark.asyncio
async def test_import_requires_cached_canadian_company(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    service = OpenFilingsService(cache)

    with pytest.raises(CompanyNotFoundError, match="Search and cache"):
        await service.import_sedar_filing(
            _company().id,
            document_url=DOCUMENT_URL,
            document_data=b"%PDF-1.7 filing",
            title="Annual Report",
            filing_date=date(2026, 3, 12),
        )
    cache.close()


@pytest.mark.asyncio
async def test_rejected_import_leaves_no_dangling_filing(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    cache.put_companies([_company()])
    service = OpenFilingsService(cache, cache_max_mb=0)

    with pytest.raises(DocumentUnavailableError, match="cache budget"):
        await service.import_sedar_filing(
            _company().id,
            document_url=DOCUMENT_URL,
            document_data=b"%PDF-1.7 filing",
            title="Annual Report",
            filing_date=date(2026, 3, 12),
        )

    assert cache.list_filings(_company().id) == []
    assert cache.stats().source_documents == 0
    cache.close()


def _company() -> Company:
    return Company(
        id="ca_sedar_tsx_SHOP",
        source_id="tsx:SHOP",
        name="Shopify Inc.",
        sources=("sedar",),
        market="CA",
        country_code="CA",
        ticker="SHOP",
        local_code="SHOP",
        status="active exchange-listed issuer",
        company_type="TSX",
        source_url=DOCUMENT_URL,
    )
