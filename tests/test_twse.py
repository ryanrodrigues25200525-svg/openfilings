from __future__ import annotations

from datetime import date

import httpx
import pytest

from openfilings.adapters.twse import TwseClient
from openfilings.exceptions import DocumentUnavailableError
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache

COMPANY_PATH = "/v1/opendata/t187ap03_L"
DOCUMENT_PATH = "/server-java/t57sb01"
CHINESE_FILENAME = "2025_1101_20260603F04.pdf"
ENGLISH_FILENAME = "2025_1101_20260603FE4.pdf"
CHINESE_URL = (
    "https://doc.twse.com.tw/server-java/t57sb01?step=9&kind=F&co_id=1101&"
    f"filename={CHINESE_FILENAME}"
)


@pytest.mark.asyncio
async def test_search_companies_uses_official_twse_listed_universe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == COMPANY_PATH
        return httpx.Response(200, json=_company_rows())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = TwseClient(client=http, today=lambda: date(2026, 7, 22))
        companies = await source.search_companies("台泥")

    assert len(companies) == 1
    company = companies[0]
    assert company.id == "tw_twse_1101"
    assert company.source_id == "1101"
    assert company.local_code == "1101"
    assert company.ticker == "1101.TW"
    assert company.name == "臺灣水泥股份有限公司"
    assert company.english_name == "TCC"
    assert company.market == "TW"
    assert company.country_code == "TW"
    assert company.sources == ("twse",)
    assert company.status == "listed issuer"


@pytest.mark.asyncio
async def test_list_filings_maps_chinese_and_english_annual_reports() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == COMPANY_PATH:
            return httpx.Response(200, json=_company_rows())
        assert request.url.path == DOCUMENT_PATH
        assert request.url.params["step"] == "1"
        assert request.url.params["co_id"] == "1101"
        assert request.url.params["year"] == "115"
        assert request.url.params["mtype"] == "F"
        return httpx.Response(200, content=_report_html())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = TwseClient(client=http, today=lambda: date(2026, 7, 22))
        filings = await source.list_filings("tw_twse_1101", limit=2)

    assert [filing.id for filing in filings] == [
        "tw_mops_2025_1101_20260603FE4",
        "tw_mops_2025_1101_20260603F04",
    ]
    english, chinese = filings
    assert chinese.source == "twse"
    assert chinese.company_id == "tw_twse_1101"
    assert chinese.category == "accounts"
    assert chinese.filing_type == "annual"
    assert chinese.period_end == date(2025, 12, 31)
    assert chinese.filing_date == date(2026, 5, 20)
    assert chinese.document_id == CHINESE_URL
    assert chinese.pdf_available is True
    assert chinese.xbrl_available is False
    assert chinese.language == "zh"
    assert english.filing_type == "annual_english"
    assert english.language == "en"


@pytest.mark.asyncio
async def test_get_filing_resolves_metadata_from_stable_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == COMPANY_PATH
        return httpx.Response(200, json=_company_rows())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = TwseClient(client=http)
        filing = await source.get_filing("tw_mops_2025_1101_20260603F04")

    assert filing.company_id == "tw_twse_1101"
    assert filing.issuer_name == "臺灣水泥股份有限公司"
    assert filing.period_end == date(2025, 12, 31)
    assert filing.filing_date == date(2026, 6, 3)
    assert filing.document_id == CHINESE_URL


@pytest.mark.asyncio
async def test_download_document_recognizes_mislabeled_pdf() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == DOCUMENT_PATH
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"%PDF-1.7 Taiwan annual report",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = TwseClient(client=http)
        document = await source.download_document(CHINESE_URL)

    assert document.data.startswith(b"%PDF")
    assert document.media_type == "application/pdf"
    assert document.source_url == CHINESE_URL


@pytest.mark.asyncio
async def test_download_document_follows_valid_twse_pdf_handoff() -> None:
    pdf_path = "/pdf/2025_1101_20260603F04_20260723_034142.pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DOCUMENT_PATH:
            return httpx.Response(
                200,
                content=(
                    "<html><body>File: "
                    f"<a href='{pdf_path}'>{CHINESE_FILENAME}</a>"
                    "</body></html>"
                ).encode("big5"),
            )
        assert request.url.path == pdf_path
        return httpx.Response(200, content=b"%PDF-1.7 handed-off report")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = TwseClient(client=http)
        document = await source.download_document(CHINESE_URL)

    assert document.data == b"%PDF-1.7 handed-off report"
    assert document.source_url == CHINESE_URL


def test_document_url_rejects_external_hosts() -> None:
    with pytest.raises(DocumentUnavailableError, match="Unsafe"):
        TwseClient.document_url("https://example.test/report.pdf")


@pytest.mark.asyncio
async def test_service_runs_complete_twse_search_list_and_markdown_pipeline(
    tmp_path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == COMPANY_PATH:
            return httpx.Response(200, json=_company_rows())
        if request.url.params.get("step") == "1":
            return httpx.Response(200, content=_report_html())
        if request.url.params.get("step") == "9":
            return httpx.Response(200, content=b"%PDF-1.7 Taiwan report")
        raise AssertionError(f"Unexpected TWSE request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        twse = TwseClient(client=http, today=lambda: date(2026, 7, 22))
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(
            cache,
            twse_source=twse,
            converter=lambda _: "## Financial statements\n\nRevenue was NT$100.",
        )

        company = await service.company("TCC", source="twse")
        filings = await company.get_filings(source="twse", limit=1)
        filing = filings.latest()
        assert filing is not None
        content = await filing.markdown()
        cache.close()

    assert company.id == "tw_twse_1101"
    assert filing.id == "tw_mops_2025_1101_20260603FE4"
    assert "## Financial statements" in content
    assert "Source system: `twse`" in content


def _company_rows() -> list[dict[str, str]]:
    return [
        {
            "出表日期": "1150721",
            "公司代號": "1101",
            "公司名稱": "臺灣水泥股份有限公司",
            "公司簡稱": "台泥",
            "產業別": "01",
            "住址": "台北市中山北路2段113號",
            "營利事業統一編號": "11913502",
            "上市日期": "19620209",
            "英文簡稱": "TCC",
            "英文通訊地址": "No. 113, Sec. 2, Zhongshan N. Rd., Taipei",
            "網址": "https://www.tccgroupholdings.com/",
        },
        {
            "出表日期": "1150721",
            "公司代號": "2330",
            "公司名稱": "台灣積體電路製造股份有限公司",
            "公司簡稱": "台積電",
            "產業別": "24",
            "住址": "新竹科學園區力行六路8號",
            "營利事業統一編號": "22099131",
            "上市日期": "19940905",
            "英文簡稱": "TSMC",
            "英文通訊地址": "No. 8, Li-Hsin Rd. 6, Hsinchu",
            "網址": "https://www.tsmc.com",
        },
    ]


def _report_html() -> bytes:
    html = f"""
    <html><body><table>
      <tr>
        <td>1101</td><td>115 年</td><td>股東會相關資料</td><td>&nbsp;</td>
        <td>常會</td><td>開會通知</td><td>&nbsp;</td>
        <td><a
          href='javascript:readfile2("F","1101","2026_1101_20260603F01.pdf");'
        >notice.pdf</a></td>
        <td>100</td><td>115/05/01 09:00:00</td><td>無</td>
      </tr>
      <tr>
        <td>1101</td><td>114 年</td><td>股東會相關資料</td><td>&nbsp;</td>
        <td>常會</td><td>股東會年報(尚未適用永續揭露準則)</td><td>&nbsp;</td>
        <td><a
          href='javascript:readfile2("F","1101","{CHINESE_FILENAME}");'
        >{CHINESE_FILENAME}</a></td>
        <td>7000000</td><td>115/05/20 19:02:58</td><td>無</td>
      </tr>
      <tr>
        <td>1101</td><td>114 年</td><td>股東會相關資料</td><td>&nbsp;</td>
        <td>常會</td><td>英文版-股東會年報(尚未適用永續揭露準則)</td><td>&nbsp;</td>
        <td><a
          href='javascript:readfile2("F","1101","{ENGLISH_FILENAME}");'
        >{ENGLISH_FILENAME}</a></td>
        <td>6800000</td><td>115/05/20 19:03:30</td><td>無</td>
      </tr>
    </table></body></html>
    """
    return html.encode("big5", errors="xmlcharrefreplace")
