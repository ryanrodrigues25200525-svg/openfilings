from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from openfilings.adapters.asx import AsxClient
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache

LISTED_COMPANIES_PATH = "/asx/research/ASXListedCompanies.csv"
ANNOUNCEMENT_LIST_PATH = "/asx/1/announcement/list"
PDF_URL = "https://announcements.asx.com.au/asxpdf/20260716/pdf/071rnjvs39bf7r.pdf"


def _now() -> datetime:
    return datetime(2026, 7, 24, tzinfo=UTC)


@pytest.mark.asyncio
async def test_search_companies_matches_code_and_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == LISTED_COMPANIES_PATH
        return httpx.Response(200, content=_listed_companies_csv())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = AsxClient(client=http)
        by_code = await source.search_companies("BHP")
        by_name = await source.search_companies("BHP Group")
        excluded = await source.search_companies("Nestle")

    assert excluded == []
    assert [company.id for company in by_code] == ["au_asx_BHP"]
    assert [company.id for company in by_name] == ["au_asx_BHP"]
    company = by_code[0]
    assert company.source_id == "BHP"
    assert company.name == "BHP GROUP LIMITED"
    assert company.market == "AU"
    assert company.country_code == "AU"
    assert company.ticker == "BHP.AX"
    assert company.local_code == "BHP"
    assert company.sources == ("asx",)
    assert company.source_url == "https://www.asx.com.au/markets/company/BHP"


@pytest.mark.asyncio
async def test_list_filings_pages_backward_and_filters_to_financial_reports() -> None:
    requested_end_dates: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == LISTED_COMPANIES_PATH:
            return httpx.Response(200, content=_listed_companies_csv())
        assert request.url.path == ANNOUNCEMENT_LIST_PATH
        end_date = request.url.params["end_date"]
        requested_end_dates.append(end_date)
        if len(requested_end_dates) == 1:
            return httpx.Response(200, json=_recent_page())
        return httpx.Response(200, json=_older_page())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = AsxClient(client=http, now=_now)
        filings = await source.list_filings("au_asx_BHP")

    assert len(requested_end_dates) == 2
    assert requested_end_dates[1] == str(
        int(datetime(2026, 7, 20, 9, 0, 0, tzinfo=UTC).timestamp() * 1000) - 1
    )
    assert len(filings) == 1
    filing = filings[0]
    assert filing.id == "au_asx_filing_03111504"
    assert filing.company_id == "au_asx_BHP"
    assert filing.source == "asx"
    assert filing.source_id == "03111504"
    assert filing.title == "Preliminary Final Report"
    assert filing.category == "accounts"
    assert filing.filing_type == "annual"
    assert filing.pages == 40
    assert filing.document_id == PDF_URL
    assert filing.media_type == "application/pdf"
    assert filing.issuer_name == "BHP GROUP LIMITED"
    assert filing.pdf_available is True
    assert filing.xbrl_available is False
    assert filing.source_url == PDF_URL


@pytest.mark.asyncio
async def test_list_filings_rejects_unknown_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_listed_companies_csv())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = AsxClient(client=http, now=_now)
        with pytest.raises(SourceError, match="not a current listed issuer"):
            await source.list_filings("au_asx_ZZZ")


@pytest.mark.asyncio
async def test_list_filings_ignores_non_financial_category() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_listed_companies_csv())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = AsxClient(client=http, now=_now)
        filings = await source.list_filings("au_asx_BHP", category="disclosure")

    assert filings == []


@pytest.mark.asyncio
async def test_download_document_validates_pdf_host_and_magic_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == PDF_URL
        return httpx.Response(200, content=b"%PDF-1.4 ASX report")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = AsxClient(client=http)
        document = await source.download_document(PDF_URL)

    assert document.data.startswith(b"%PDF")
    assert document.media_type == "application/pdf"
    assert document.source_url == PDF_URL


def test_document_url_rejects_external_hosts_and_unexpected_paths() -> None:
    with pytest.raises(DocumentUnavailableError, match="Unsafe"):
        AsxClient.document_url("https://example.test/report.pdf")
    with pytest.raises(DocumentUnavailableError, match="Unsafe"):
        AsxClient.document_url(
            "https://announcements.asx.com.au/asxpdf/20260716/pdf/../secret.pdf"
        )


@pytest.mark.asyncio
async def test_service_runs_complete_asx_search_list_and_markdown_pipeline(
    tmp_path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == LISTED_COMPANIES_PATH:
            return httpx.Response(200, content=_listed_companies_csv())
        if request.url.path == ANNOUNCEMENT_LIST_PATH:
            return httpx.Response(200, json=_recent_page())
        assert str(request.url) == PDF_URL
        return httpx.Response(200, content=b"%PDF-1.4 ASX report")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        asx = AsxClient(client=http, now=_now)
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(
            cache,
            market_sources=(asx,),
            converter=lambda _: "## Financial statements\n\nRevenue was AUD 1.0B.",
        )

        company = await service.company("BHP", source="asx")
        filings = await company.get_filings(source="asx", limit=1)
        filing = filings.latest()
        assert filing is not None
        content = await filing.markdown()
        cache.close()

    assert company.id == "au_asx_BHP"
    assert filing.id == "au_asx_filing_03111504"
    assert "## Financial statements" in content
    assert "Source system: `asx`" in content


def _listed_companies_csv() -> bytes:
    return (
        b"ASX listed companies as at Fri Jul 24 05:07:04 AEST 2026\n"
        b"\n"
        b"Company name,ASX code,GICS industry group\n"
        b'"BHP GROUP LIMITED","BHP","Materials"\n'
        b'"CSL LIMITED","CSL","Pharmaceuticals, Biotechnology & Life Sciences"\n'
    )


def _recent_page() -> dict[str, object]:
    return {
        "announcement_data": [
            {
                "id": "03111504",
                "document_release_date": "2026-07-20T19:00:00+1000",
                "url": PDF_URL,
                "header": "Preliminary Final Report",
                "market_sensitive": True,
                "number_of_pages": 40,
                "size": "633KB",
                "issuer_code": "BHP",
                "issuer_full_name": "BHP GROUP LIMITED",
            },
            {
                "id": "03111600",
                "document_release_date": "2026-07-21T10:00:00+1000",
                "url": (
                    "https://announcements.asx.com.au/asxpdf/20260721/"
                    "pdf/071other0000.pdf"
                ),
                "header": "Change in substantial holding",
                "market_sensitive": False,
                "number_of_pages": 3,
                "size": "50KB",
                "issuer_code": "CSL",
                "issuer_full_name": "CSL LIMITED",
            },
        ]
    }


def _older_page() -> dict[str, object]:
    return {"announcement_data": []}
