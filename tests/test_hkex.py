from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from xml.sax.saxutils import escape

import httpx
import pytest

from openfilings.adapters.hkex import HkexClient
from openfilings.exceptions import DocumentUnavailableError
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache

SECURITIES_PATH = (
    "/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"
)
PREFIX_PATH = "/search/prefix.do"
SEARCH_PATH = "/search/titleSearchServlet.do"
ANNUAL_PATH = "/listedco/listconews/sehk/2026/0316/2026031600319.pdf"
ANNUAL_URL = f"https://www1.hkexnews.hk{ANNUAL_PATH}"


@pytest.mark.asyncio
async def test_search_companies_uses_only_hkex_listed_issuer_equities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == SECURITIES_PATH
        return httpx.Response(200, content=_securities_workbook())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = HkexClient(client=http)
        companies = await source.search_companies("HKEX")

    assert len(companies) == 1
    company = companies[0]
    assert company.id == "hk_hkex_00388"
    assert company.source_id == "00388"
    assert company.local_code == "00388"
    assert company.ticker == "0388.HK"
    assert company.name == "HKEX"
    assert company.market == "HK"
    assert company.country_code == "HK"
    assert company.sources == ("hkex",)
    assert company.status == "listed issuer"


@pytest.mark.asyncio
async def test_search_companies_accepts_stable_id_and_gem_code() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_securities_workbook())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = HkexClient(client=http)
        exact = await source.search_companies("hk_hkex_00388")
        gem = await source.search_companies("08123")

    assert [company.id for company in exact] == ["hk_hkex_00388"]
    assert [company.id for company in gem] == ["hk_hkex_08123"]


@pytest.mark.asyncio
async def test_list_filings_maps_annual_and_interim_reports() -> None:
    requested_categories: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SECURITIES_PATH:
            return httpx.Response(200, content=_securities_workbook())
        if request.url.path == PREFIX_PATH:
            assert request.url.params["name"] == "00388"
            return httpx.Response(
                200,
                text='callback({"more":"0","stockInfo":'
                '[{"stockId":781,"code":"00388","name":"HKEX"}]});',
            )
        assert request.url.path == SEARCH_PATH
        assert request.url.params["stockId"] == "781"
        assert request.url.params["t1code"] == "40000"
        category = request.url.params["t2code"]
        requested_categories.add(category)
        rows = [_annual_row()] if category == "40100" else [_interim_row()]
        return httpx.Response(200, json=_search_response(rows))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = HkexClient(client=http, today=lambda: date(2026, 7, 23))
        filings = await source.list_filings("hk_hkex_00388", limit=2)

    assert requested_categories == {"40100", "40200"}
    assert [filing.id for filing in filings] == [
        "hk_hkex_12052683",
        "hk_hkex_11800001",
    ]
    annual, interim = filings
    assert annual.company_id == "hk_hkex_00388"
    assert annual.source == "hkex"
    assert annual.source_id == "12052683"
    assert annual.title == "2025 Annual Report"
    assert annual.category == "accounts"
    assert annual.filing_type == "annual"
    assert annual.filing_date == date(2026, 3, 16)
    assert annual.document_id == ANNUAL_URL
    assert annual.media_type == "application/pdf"
    assert annual.issuer_name == "HKEX"
    assert annual.pdf_available is True
    assert annual.xbrl_available is False
    assert interim.filing_type == "interim"


@pytest.mark.asyncio
async def test_download_document_requires_a_real_hkex_pdf() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == ANNUAL_PATH
        return httpx.Response(200, content=b"%PDF-1.7 HKEX annual report")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = HkexClient(client=http)
        document = await source.download_document(ANNUAL_URL)

    assert document.data.startswith(b"%PDF")
    assert document.media_type == "application/pdf"
    assert document.source_url == ANNUAL_URL


def test_document_url_rejects_external_hosts_and_unexpected_paths() -> None:
    with pytest.raises(DocumentUnavailableError, match="Unsafe"):
        HkexClient.document_url("https://example.test/report.pdf")
    with pytest.raises(DocumentUnavailableError, match="Unsafe"):
        HkexClient.document_url("https://www1.hkexnews.hk/search/report.pdf")


@pytest.mark.asyncio
async def test_service_runs_complete_hkex_search_list_and_markdown_pipeline(
    tmp_path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SECURITIES_PATH:
            return httpx.Response(200, content=_securities_workbook())
        if request.url.path == PREFIX_PATH:
            return httpx.Response(
                200,
                text='callback({"more":"0","stockInfo":'
                '[{"stockId":781,"code":"00388","name":"HKEX"}]});',
            )
        if request.url.path == SEARCH_PATH:
            rows = [_annual_row()] if request.url.params["t2code"] == "40100" else []
            return httpx.Response(200, json=_search_response(rows))
        if request.url.path == ANNUAL_PATH:
            return httpx.Response(200, content=b"%PDF-1.7 HKEX report")
        raise AssertionError(f"Unexpected HKEX request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        hkex = HkexClient(client=http, today=lambda: date(2026, 7, 23))
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(
            cache,
            hkex_source=hkex,
            converter=lambda _: "## Financial statements\n\nRevenue was HK$100.",
        )

        company = await service.company("HKEX", source="hkex")
        filings = await company.get_filings(source="hkex", limit=1)
        filing = filings.latest()
        assert filing is not None
        content = await filing.markdown()
        cache.close()

    assert company.id == "hk_hkex_00388"
    assert filing.id == "hk_hkex_12052683"
    assert "## Financial statements" in content
    assert "Source system: `hkex`" in content


def _securities_workbook() -> bytes:
    rows = [
        ("00388", "HKEX", "Equity", "Equity Securities (Main Board)", "HK0388045442"),
        ("80388", "HKEX-R", "Equity", "Equity Securities (Main Board)", "HK0388045442"),
        ("08123", "GEM ISSUER", "Equity", "Equity Securities (GEM)", "KYG000001234"),
        (
            "02800",
            "TRACKER FUND",
            "Exchange Traded Products",
            "Exchange Traded Funds",
            "HK2800008867",
        ),
        ("00900", "INVESTMENT CO", "Equity", "Investment Companies", "HK0900000001"),
    ]
    sheet_rows = ['<row r="3"><c r="A3" t="str"><v>Stock Code</v></c></row>']
    for index, (code, name, category, subcategory, isin) in enumerate(rows, start=4):
        values = (code, name, category, subcategory, isin)
        cells = "".join(
            f'<c r="{column}{index}" t="str"><v>{escape(value)}</v></c>'
            for column, value in zip(("A", "B", "C", "D", "F"), values, strict=True)
        )
        sheet_rows.append(f'<row r="{index}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def _annual_row() -> dict[str, str]:
    return {
        "FILE_INFO": "14MB",
        "NEWS_ID": "12052683",
        "SHORT_TEXT": (
            "Financial Statements&#x2f;ESG Information - [Annual Report]<br/>"
        ),
        "STOCK_NAME": "HKEX<br/>HKEX-R",
        "TITLE": "2025 Annual Report",
        "FILE_TYPE": "PDF",
        "DATE_TIME": "16/03/2026 12:00",
        "LONG_TEXT": "Financial Statements&#x2f;ESG Information - [Annual Report]",
        "STOCK_CODE": "00388<br/>80388",
        "FILE_LINK": ANNUAL_PATH,
    }


def _interim_row() -> dict[str, str]:
    return {
        "FILE_INFO": "8MB",
        "NEWS_ID": "11800001",
        "SHORT_TEXT": (
            "Financial Statements&#x2f;ESG Information - [Interim/Half-Year Report]"
        ),
        "STOCK_NAME": "HKEX",
        "TITLE": "2025 Interim Report",
        "FILE_TYPE": "PDF",
        "DATE_TIME": "20/08/2025 12:00",
        "LONG_TEXT": (
            "Financial Statements&#x2f;ESG Information - [Interim/Half-Year Report]"
        ),
        "STOCK_CODE": "00388",
        "FILE_LINK": "/listedco/listconews/sehk/2025/0820/2025082000001.pdf",
    }


def _search_response(rows: list[dict[str, str]]) -> dict[str, object]:
    return {
        "result": json.dumps(rows),
        "hasNextRow": False,
        "rowRange": 100,
        "lang": "E",
        "loadedRecord": len(rows),
        "recordCnt": len(rows),
    }
