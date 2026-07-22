from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from openfilings.adapters.sgx import SgxClient
from openfilings.exceptions import DocumentUnavailableError
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache

STOCKS_PATH = "/securities/v1.1/stocks"
METADATA_PATH = "/marketmetadata/v2"
REPORTS_PATH = "/financialreports/v1.0"
DETAIL_PATH = (
    "/1.0.0/corporate-announcements/2J4PCEOQYA3WTBWP/"
    "7a29b3617781ade440676c0c7766b81c543309a4f37bfa8bf1707aecfa39f131"
)
DETAIL_URL = f"https://links.sgx.com{DETAIL_PATH}"
PDF_PATH = (
    "/1.0.0/corporate-announcements/2J4PCEOQYA3WTBWP/859054_2025_SGX_Annual_Report.pdf"
)
PDF_URL = f"https://links.sgx.com{PDF_PATH}"


@pytest.mark.asyncio
async def test_search_companies_joins_only_mainboard_and_catalist_stocks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == STOCKS_PATH:
            return httpx.Response(200, json=_stocks_response())
        if request.url.path == METADATA_PATH:
            return httpx.Response(200, json=_metadata_response())
        raise AssertionError(f"Unexpected SGX request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = SgxClient(client=http)
        companies = await source.search_companies("S68")
        excluded = await source.search_companies("GLOBALCO")

    assert excluded == []
    assert len(companies) == 1
    company = companies[0]
    assert company.id == "sg_sgx_1J26"
    assert company.source_id == "1J26"
    assert company.local_code == "S68"
    assert company.ticker == "S68.SI"
    assert company.name == "SINGAPORE EXCHANGE LIMITED"
    assert company.market == "SG"
    assert company.country_code == "SG"
    assert company.sources == ("sgx",)
    assert company.status == "Mainboard listed issuer"


@pytest.mark.asyncio
async def test_search_companies_accepts_stable_id_and_issuer_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == STOCKS_PATH:
            return httpx.Response(200, json=_stocks_response())
        return httpx.Response(200, json=_metadata_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = SgxClient(client=http)
        exact = await source.search_companies("sg_sgx_1J26")
        named = await source.search_companies("Singapore Exchange")

    assert [company.id for company in exact] == ["sg_sgx_1J26"]
    assert [company.id for company in named] == ["sg_sgx_1J26"]


@pytest.mark.asyncio
async def test_list_filings_maps_sgx_annual_reports() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == STOCKS_PATH:
            return httpx.Response(200, json=_stocks_response())
        if request.url.path == METADATA_PATH:
            return httpx.Response(200, json=_metadata_response())
        assert request.url.path == REPORTS_PATH
        assert request.url.params["companyname"] == "SINGAPORE EXCHANGE LIMITED"
        assert request.url.params["pagestart"] == "0"
        return httpx.Response(200, json=_reports_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = SgxClient(client=http, today=lambda: date(2026, 7, 23))
        filings = await source.list_filings("sg_sgx_1J26")

    assert len(filings) == 1
    filing = filings[0]
    assert filing.id == "sg_sgx_2J4PCEOQYA3WTBWP"
    assert filing.company_id == "sg_sgx_1J26"
    assert filing.source == "sgx"
    assert filing.source_id == "2J4PCEOQYA3WTBWP"
    assert filing.title == "2025 Annual Report"
    assert filing.category == "accounts"
    assert filing.filing_type == "annual"
    assert filing.filing_date == date(2025, 9, 15)
    assert filing.period_end == date(2025, 6, 30)
    assert filing.published_at == datetime(2025, 9, 15, 1, 28, 24, tzinfo=UTC)
    assert filing.document_id == DETAIL_URL
    assert filing.media_type == "application/pdf"
    assert filing.issuer_name == "SINGAPORE EXCHANGE LIMITED"
    assert filing.pdf_available is True
    assert filing.xbrl_available is False


@pytest.mark.asyncio
async def test_download_document_selects_annual_report_attachment() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == DETAIL_PATH:
            return httpx.Response(200, text=_detail_html())
        if request.url.path == PDF_PATH:
            return httpx.Response(200, content=b"%PDF-1.7 SGX annual report")
        raise AssertionError(f"Unexpected SGX request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = SgxClient(client=http)
        document = await source.download_document(DETAIL_URL)

    assert requested_paths == [DETAIL_PATH, PDF_PATH]
    assert document.data.startswith(b"%PDF")
    assert document.media_type == "application/pdf"
    assert document.source_url == PDF_URL


def test_detail_url_rejects_external_hosts_and_unexpected_paths() -> None:
    with pytest.raises(DocumentUnavailableError, match="Unsafe"):
        SgxClient.detail_url("https://example.test/report")
    with pytest.raises(DocumentUnavailableError, match="Unsafe"):
        SgxClient.detail_url("https://links.sgx.com/1.0.0/../../report")


@pytest.mark.asyncio
async def test_download_document_rejects_sustainability_only_page() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                '<a href="/1.0.0/corporate-announcements/'
                '2J4PCEOQYA3WTBWP/859055_2025_Sustainability_Report.pdf">'
                "Sustainability Report</a>"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = SgxClient(client=http)
        with pytest.raises(DocumentUnavailableError, match="safe PDF"):
            await source.download_document(DETAIL_URL)


@pytest.mark.asyncio
async def test_service_runs_complete_sgx_search_list_and_markdown_pipeline(
    tmp_path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == STOCKS_PATH:
            return httpx.Response(200, json=_stocks_response())
        if request.url.path == METADATA_PATH:
            return httpx.Response(200, json=_metadata_response())
        if request.url.path == REPORTS_PATH:
            return httpx.Response(200, json=_reports_response())
        if request.url.path == DETAIL_PATH:
            return httpx.Response(200, text=_detail_html())
        if request.url.path == PDF_PATH:
            return httpx.Response(200, content=b"%PDF-1.7 SGX report")
        raise AssertionError(f"Unexpected SGX request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sgx = SgxClient(client=http, today=lambda: date(2026, 7, 23))
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(
            cache,
            sgx_source=sgx,
            converter=lambda _: "## Financial statements\n\nRevenue was S$100.",
        )

        company = await service.company("S68", source="sgx")
        filings = await company.get_filings(source="sgx", limit=1)
        filing = filings.latest()
        assert filing is not None
        content = await filing.markdown()
        cache.close()

    assert company.id == "sg_sgx_1J26"
    assert filing.id == "sg_sgx_2J4PCEOQYA3WTBWP"
    assert "## Financial statements" in content
    assert "Source system: `sgx`" in content


def _stocks_response() -> dict[str, object]:
    return {
        "data": {
            "prices": [
                {"nc": "S68", "n": "SGX", "type": "stocks", "m": "MAINBOARD"},
                {"nc": "CAT1", "n": "CATALIST CO", "type": "stocks", "m": "CATALIST"},
                {
                    "nc": "GQ1",
                    "n": "GLOBALCO",
                    "type": "stocks",
                    "m": "GLOBAL_QUOTE",
                },
            ]
        }
    }


def _metadata_response() -> dict[str, object]:
    return {
        "meta": {"code": "200", "message": "success"},
        "data": [
            {
                "stockCode": "S68",
                "issuerName": "SINGAPORE EXCHANGE LIMITED",
                "ibmCode": "1J26",
                "isinCode": "SG1J26887955",
            },
            {
                "stockCode": "CAT1",
                "issuerName": "CATALIST COMPANY LIMITED",
                "ibmCode": "9Z99",
                "isinCode": "SG0000000001",
            },
            {
                "stockCode": "GQ1",
                "issuerName": "GLOBALCO INC",
                "ibmCode": "8Y88",
                "isinCode": "US0000000001",
            },
        ],
    }


def _reports_response() -> dict[str, object]:
    return {
        "meta": {
            "code": "200",
            "message": "success",
            "totalPages": 1,
            "totalItems": 3,
        },
        "data": [
            {
                "documentDate": 1_751_212_800_000,
                "securityName": "SINGAPORE EXCHANGE LIMITED",
                "companyName": "SINGAPORE EXCHANGE LIMITED",
                "title": "Annual Report",
                "url": DETAIL_URL,
                "id": "2J4PCEOQYA3WTBWP",
                "broadcastDateTime": 1_757_899_704_000,
            },
            {
                "documentDate": 1_751_212_800_000,
                "companyName": "SINGAPORE EXCHANGE LIMITED",
                "title": "Sustainability Report",
                "url": "https://links.sgx.com/ignored",
                "id": "2AAAAAAAAAAAAAAA",
                "broadcastDateTime": 1_757_899_704_000,
            },
            {
                "documentDate": 1_751_212_800_000,
                "securityName": "UNRELATED COMPANY LIMITED",
                "companyName": "UNRELATED COMPANY LIMITED",
                "title": "Annual Report",
                "url": DETAIL_URL,
                "id": "2J4PCEOQYA3WTBWP",
                "broadcastDateTime": 1_757_899_704_000,
            },
        ],
    }


def _detail_html() -> str:
    return f"""
    <html><body>
      <a href="/1.0.0/corporate-announcements/2J4PCEOQYA3WTBWP/
859055_2025_SGX_Sustainability_Report.pdf">Sustainability Report</a>
      <a href="{PDF_PATH}">2025 SGX Annual Report</a>
      <a href="https://example.test/malicious.pdf">Annual Report mirror</a>
    </body></html>
    """
