from __future__ import annotations

import httpx
import pytest

from openfilings.adapters.asx import LISTED_COMPANIES_URL, AsxClient
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache

LISTED_COMPANIES_PATH = httpx.URL(LISTED_COMPANIES_URL).path


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
async def test_search_companies_reads_current_directory_csv_layout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == LISTED_COMPANIES_PATH
        return httpx.Response(
            200,
            content=(
                b'"ASX code","Company name","GICs industry group",'
                b'"Listing date","Market Cap"\n'
                b'"BHP","BHP GROUP LIMITED","Materials","01/01/1990",1\n'
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = AsxClient(client=http)
        companies = await source.search_companies("BHP")

    assert [company.id for company in companies] == ["au_asx_BHP"]
    assert companies[0].company_type == "Materials"


@pytest.mark.asyncio
async def test_list_filings_explains_that_australia_is_discovery_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("discovery-only ASX must not issue filing requests")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = AsxClient(client=http)
        with pytest.raises(SourceError, match="no keyless source"):
            await source.list_filings("au_asx_BHP")


@pytest.mark.asyncio
async def test_list_filings_rejects_a_malformed_company_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a malformed ID must fail before any request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = AsxClient(client=http)
        with pytest.raises(SourceError, match="shaped like au_asx_BHP"):
            await source.list_filings("not-an-asx-id")


@pytest.mark.asyncio
async def test_download_document_reports_australia_as_discovery_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("discovery-only ASX must not fetch documents")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = AsxClient(client=http)
        with pytest.raises(DocumentUnavailableError, match="discovery-only"):
            await source.download_document("au_asx_filing_03111504")


@pytest.mark.asyncio
async def test_service_searches_asx_companies_and_reports_no_filing_source(
    tmp_path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == LISTED_COMPANIES_PATH
        return httpx.Response(200, content=_listed_companies_csv())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        asx = AsxClient(client=http)
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(cache, market_sources=(asx,))

        company = await service.company("BHP", source="asx")
        with pytest.raises(SourceError, match="no keyless source"):
            await company.get_filings(source="asx", limit=1)
        cache.close()

    assert company.id == "au_asx_BHP"
    assert company.name == "BHP GROUP LIMITED"


def _listed_companies_csv() -> bytes:
    return (
        b"ASX listed companies as at Fri Jul 24 05:07:04 AEST 2026\n"
        b"\n"
        b"Company name,ASX code,GICS industry group\n"
        b'"BHP GROUP LIMITED","BHP","Materials"\n'
        b'"CSL LIMITED","CSL","Pharmaceuticals, Biotechnology & Life Sciences"\n'
    )
