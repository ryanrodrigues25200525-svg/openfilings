from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal

import httpx
import pytest

from openfilings.adapters.dart import DartClient
from openfilings.exceptions import ConfigurationError, DocumentUnavailableError
from openfilings.models import Filing
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache
from openfilings.xbrl.dart_structured import extract_dart_structured_financials

CORP_CODE_PATH = "/api/corpCode.xml"
LIST_PATH = "/api/list.json"
DOCUMENT_PATH = "/api/document.xml"
FINANCIALS_PATH = "/api/fnlttSinglAcntAll.json"


@pytest.mark.asyncio
async def test_search_companies_requires_an_api_key() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    ) as http:
        client = DartClient(client=http)
        with pytest.raises(ConfigurationError, match="DART_API_KEY"):
            await client.search_companies("005930")


@pytest.mark.asyncio
async def test_search_companies_parses_corp_code_archive_and_ranks_matches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == CORP_CODE_PATH
        assert request.url.params["crtfc_key"] == "secret-key"
        return httpx.Response(200, content=_corp_code_archive())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DartClient("secret-key", client=http)
        companies = await client.search_companies("005930")

    assert len(companies) == 1
    company = companies[0]
    assert company.id == "kr_dart_00126380"
    assert company.source_id == "00126380"
    assert company.local_code == "00126380"
    assert company.ticker == "005930"
    assert company.name == "삼성전자"
    assert company.market == "KR"
    assert company.country_code == "KR"
    assert company.sources == ("dart",)


@pytest.mark.asyncio
async def test_list_filings_maps_periodic_reports_and_skips_other_disclosures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == LIST_PATH
        assert request.url.params["corp_code"] == "00126380"
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "success",
                "list": [
                    _list_row(
                        rcept_no="20260401000001",
                        report_nm="사업보고서 (2025.12)",
                        rcept_dt="20260401",
                    ),
                    _list_row(
                        rcept_no="20260601000002",
                        report_nm="주요사항보고서(자기주식취득결정)",
                        rcept_dt="20260601",
                    ),
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DartClient("secret-key", client=http)
        filings = await client.list_filings("kr_dart_00126380")

    assert [filing.id for filing in filings] == ["kr_dart_filing_20260401000001"]
    filing = filings[0]
    assert filing.company_id == "kr_dart_00126380"
    assert filing.source == "dart"
    assert filing.filing_type == "annual"
    assert filing.category == "accounts"
    assert filing.period_end == date(2025, 12, 31)
    assert filing.document_id == "20260401000001"


@pytest.mark.asyncio
async def test_list_filings_treats_no_data_status_as_empty() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "013", "message": "no data found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DartClient("secret-key", client=http)
        filings = await client.list_filings("kr_dart_00126380")

    assert filings == []


@pytest.mark.asyncio
async def test_download_document_returns_zip_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == DOCUMENT_PATH
        assert request.url.params["rcept_no"] == "20260401000001"
        return httpx.Response(
            200,
            headers={"content-type": "application/x-msdownload"},
            content=b"PK\x03\x04fake-zip",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DartClient("secret-key", client=http)
        document = await client.download_document("20260401000001")

    assert document.data == b"PK\x03\x04fake-zip"
    assert document.media_type == "application/zip"


@pytest.mark.asyncio
async def test_download_document_raises_on_json_error_response() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            json={"status": "020", "message": "usage limit exceeded"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = DartClient("secret-key", client=http)
        with pytest.raises(DocumentUnavailableError, match="usage limit exceeded"):
            await client.download_document("20260401000001")


def test_extract_dart_structured_financials_builds_balance_sheet_identity() -> None:
    from openfilings.models import ReportingPeriod

    period = ReportingPeriod(
        id="dart-fy-2025-12-31",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        kind="duration",
        fiscal_period="FY",
    )
    rows = [
        _financial_row("BS", "ifrs-full_Assets", "1000"),
        _financial_row("BS", "ifrs-full_Liabilities", "400"),
        _financial_row("BS", "ifrs-full_Equity", "600"),
        _financial_row("IS", "ifrs-full_Revenue", "500"),
        _financial_row("IS", "ifrs-full_ProfitLoss", "50"),
        _financial_row("BS", "dart_CompanySpecificExtension", "999"),
    ]

    financials = extract_dart_structured_financials(
        rows,
        _filing(),
        period=period,
        source_url="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        sha256="a" * 64,
    )

    assert financials.extraction_method == "dart-fnlttsinglacntall"
    balance = financials.balance_sheet()
    assert balance is not None
    by_code = {item.code: item.values[0].value for item in balance.line_items}
    assert by_code["total_assets"] == Decimal("1000")
    assert by_code["total_liabilities"] == Decimal("400")
    assert by_code["total_equity"] == Decimal("600")
    total = by_code["total_liabilities"] + by_code["total_equity"]
    assert total == by_code["total_assets"]
    assert balance.currency == "KRW"

    income = financials.income_statement()
    assert income is not None
    net_income = next(
        item for item in income.line_items if item.code == "net_income_loss"
    )
    assert net_income.values[0].value == Decimal("50")


@pytest.mark.asyncio
async def test_service_prefers_dart_structured_financials_over_document_download(
    tmp_path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == CORP_CODE_PATH:
            return httpx.Response(200, content=_corp_code_archive())
        if request.url.path == LIST_PATH:
            return httpx.Response(
                200,
                json={
                    "status": "000",
                    "message": "success",
                    "list": [
                        _list_row(
                            rcept_no="20260401000001",
                            report_nm="사업보고서 (2025.12)",
                            rcept_dt="20260401",
                        )
                    ],
                },
            )
        if request.url.path == FINANCIALS_PATH:
            assert request.url.params["fs_div"] == "CFS"
            return httpx.Response(
                200,
                json={
                    "status": "000",
                    "message": "success",
                    "list": [
                        _financial_row("BS", "ifrs-full_Assets", "1000"),
                        _financial_row("BS", "ifrs-full_Liabilities", "400"),
                        _financial_row("BS", "ifrs-full_Equity", "600"),
                    ],
                },
            )
        raise AssertionError(f"Unexpected DART request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        dart = DartClient("secret-key", client=http)
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(cache, dart_source=dart)

        company = await service.company("005930", source="dart")
        filings = await company.get_filings(source="dart", limit=1)
        filing = filings.latest()
        assert filing is not None
        financials = await filing.financials()
        cache.close()

    assert financials.extraction_method == "dart-fnlttsinglacntall"
    balance = financials.balance_sheet()
    assert balance is not None
    by_code = {item.code: item.values[0].value for item in balance.line_items}
    assert by_code["total_assets"] == Decimal("1000")


def _corp_code_archive() -> bytes:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list>
    <corp_code>00126380</corp_code>
    <corp_name>삼성전자</corp_name>
    <stock_code>005930</stock_code>
    <modify_date>20260101</modify_date>
  </list>
  <list>
    <corp_code>00164742</corp_code>
    <corp_name>비상장회사</corp_name>
    <stock_code></stock_code>
    <modify_date>20260101</modify_date>
  </list>
</result>"""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("CORPCODE.xml", xml.encode("utf-8"))
    return stream.getvalue()


def _list_row(*, rcept_no: str, report_nm: str, rcept_dt: str) -> dict[str, str]:
    return {
        "rcept_no": rcept_no,
        "corp_cls": "Y",
        "corp_name": "삼성전자",
        "corp_code": "00126380",
        "stock_code": "005930",
        "report_nm": report_nm,
        "rcept_dt": rcept_dt,
        "flr_nm": "삼성전자",
    }


def _financial_row(sj_div: str, account_id: str, amount: str) -> dict[str, str]:
    return {
        "rcept_no": "20260401000001",
        "reprt_code": "11011",
        "bsns_year": "2025",
        "corp_code": "00126380",
        "sj_div": sj_div,
        "sj_nm": sj_div,
        "account_id": account_id,
        "account_nm": account_id,
        "thstrm_amount": amount,
        "currency": "KRW",
    }


def _filing() -> Filing:
    return Filing(
        id="kr_dart_filing_20260401000001",
        company_id="kr_dart_00126380",
        source="dart",
        source_id="20260401000001",
        title="사업보고서 (2025.12)",
        category="accounts",
        filing_type="annual",
        filing_date=date(2026, 4, 1),
        period_end=date(2025, 12, 31),
        document_id="20260401000001",
        media_type="application/zip",
        issuer_name="삼성전자",
        language="ko",
        source_url="https://opendart.fss.or.kr/api/document.xml?rcept_no=20260401000001",
    )
