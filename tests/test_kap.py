from __future__ import annotations

import json
import struct
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from openfilings.adapters.kap import KapClient
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.models import Filing
from openfilings.xbrl.kap_structured import extract_kap_structured_financials

COMPANY_LIST_PATH = "/tr/api/company/items/IGS/A"
DISCLOSURE_LIST_PATH = "/tr/api/disclosure/members/byCriteria"
DISCLOSURE_DETAIL_PATH = "/tr/api/notification/attachment-detail/1636286"
FILE_DOWNLOAD_PATH = "/tr/api/file/download/4028328c9f52dc40019f8f9f330e3ebf"


@pytest.mark.asyncio
async def test_search_companies_matches_ticker_and_ranks_exact_first() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == COMPANY_LIST_PATH
        return httpx.Response(200, json=_company_list())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = KapClient(client=http)
        companies = await client.search_companies("DZGYO")

    assert len(companies) == 1
    company = companies[0]
    assert company.id == "tr_kap_4028e4a240ee866c0140f1fad94e0047"
    assert company.source_id == "4028e4a240ee866c0140f1fad94e0047"
    assert company.ticker == "DZGYO"
    assert company.name == "DENİZ GAYRİMENKUL YATIRIM ORTAKLIĞI A.Ş."
    assert company.market == "TR"
    assert company.country_code == "TR"
    assert company.sources == ("kap",)
    assert company.source_url == (
        "https://www.kap.org.tr/en/sirket-bilgileri/genel/"
        "912-deniz-gayrimenkul-yatirim-ortakligi-a-s"
    )


@pytest.mark.asyncio
async def test_list_filings_pages_backward_and_filters_to_financial_reports() -> None:
    requested_ranges: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == DISCLOSURE_LIST_PATH
        body = _json_body(request)
        requested_ranges.append((body["fromDate"], body["toDate"]))
        assert body["mkkMemberOidList"] == ["4028e4a240ee866c0140f1fad94e0047"]
        assert body["disclosureClass"] == "FR"
        if len(requested_ranges) == 1:
            return httpx.Response(200, json=[_financial_report_row(), _other_row()])
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = KapClient(client=http)
        filings = await client.list_filings(
            "tr_kap_4028e4a240ee866c0140f1fad94e0047", category="accounts"
        )

    assert len(requested_ranges) == 3
    assert (date.fromisoformat(requested_ranges[0][1]) - date.today()).days == 0
    assert [filing.id for filing in filings] == ["tr_kap_1636286"]
    filing = filings[0]
    assert filing.company_id == "tr_kap_4028e4a240ee866c0140f1fad94e0047"
    assert filing.source == "kap"
    assert filing.title == "Finansal Rapor"
    assert filing.category == "accounts"
    assert filing.filing_type == "financial_report"
    assert filing.period_end == date(2026, 6, 30)
    assert filing.document_id == "1636286"
    assert filing.published_at == datetime(2026, 7, 23, 19, 46, 35, tzinfo=UTC)


@pytest.mark.asyncio
async def test_list_filings_without_accounts_category_keeps_other_disclosures() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_financial_report_row(), _other_row()])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = KapClient(client=http)
        filings = await client.list_filings(
            "tr_kap_4028e4a240ee866c0140f1fad94e0047", category=None
        )

    assert {filing.id for filing in filings} == {"tr_kap_1636286", "tr_kap_1621228"}
    other = next(f for f in filings if f.id == "tr_kap_1621228")
    assert other.category == "disclosure"
    assert other.period_end is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "expected_class", "expected_id", "expected_type"),
    [
        ("material_event", "ODA", "tr_kap_1621228", "oda"),
        ("corporate_action", "", "tr_kap_1621300", "ca"),
    ],
)
async def test_list_filings_maps_public_kap_taxonomy(
    category: str,
    expected_class: str,
    expected_id: str,
    expected_type: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = _json_body(request)
        assert body["disclosureClass"] == expected_class
        return httpx.Response(
            200,
            json=[_financial_report_row(), _other_row(), _corporate_action_row()],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = KapClient(client=http)
        filings = await client.list_filings(
            "tr_kap_4028e4a240ee866c0140f1fad94e0047",
            category=category,
        )

    assert [filing.id for filing in filings] == [expected_id]
    assert filings[0].category == category
    assert filings[0].filing_type == expected_type


@pytest.mark.asyncio
async def test_list_filings_rejects_unknown_kap_category_without_request() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(AssertionError("should not request"))
        )
    ) as http:
        client = KapClient(client=http)
        filings = await client.list_filings(
            "tr_kap_4028e4a240ee866c0140f1fad94e0047",
            category="dividend",
        )

    assert filings == []


@pytest.mark.asyncio
async def test_download_document_unwraps_java_serialized_byte_array() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == DISCLOSURE_DETAIL_PATH:
            return httpx.Response(200, json=[_disclosure_detail()])
        assert request.url.path == FILE_DOWNLOAD_PATH
        return httpx.Response(200, content=_java_wrapped_pdf(b"%PDF-1.6 fake"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = KapClient(client=http)
        document = await client.download_document("1636286")

    assert document.data == b"%PDF-1.6 fake"
    assert document.media_type == "application/pdf"


@pytest.mark.asyncio
async def test_download_document_rejects_missing_attachment() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{**_disclosure_detail(), "attachments": []}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = KapClient(client=http)
        with pytest.raises(DocumentUnavailableError):
            await client.download_document("1636286")


@pytest.mark.asyncio
async def test_financial_report_bodies_returns_disclosure_body_strings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == DISCLOSURE_DETAIL_PATH
        return httpx.Response(200, json=[_disclosure_detail()])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = KapClient(client=http)
        bodies = await client.financial_report_bodies("1636286")

    assert bodies == [_BALANCE_SHEET_TABLE]


def test_normalize_company_oid_rejects_malformed_values() -> None:
    with pytest.raises(SourceError):
        KapClient.normalize_company_oid("tr_kap_not-a-real-oid")


def test_extract_kap_structured_financials_parses_balance_sheet_table() -> None:
    filing = Filing(
        id="tr_kap_1636286",
        company_id="tr_kap_4028e4a240ee866c0140f1fad94e0047",
        source="kap",
        source_id="1636286",
        title="Finansal Rapor",
        category="accounts",
        filing_type="financial_report",
        filing_date=date(2026, 7, 23),
        period_end=date(2026, 6, 30),
        issuer_name="DZGYO",
        source_url="https://www.kap.org.tr/en/Bildirim/1636286",
    )

    result = extract_kap_structured_financials(
        [_BALANCE_SHEET_TABLE],
        filing,
        source_url=filing.source_url,
        sha256="0" * 64,
    )

    assert [statement.statement_type for statement in result.statements] == [
        "balance_sheet"
    ]
    statement = result.statements[0]
    assert statement.currency == "TRY"
    assets = next(item for item in statement.line_items if item.code == "total_assets")
    values = {value.period.label: value.value for value in assets.values}
    assert values == {
        "instant 2026-06-30": Decimal("8530658585"),
        "instant 2025-12-31": Decimal("8235745263"),
    }


def test_extract_kap_structured_financials_raises_when_nothing_recognized() -> None:
    filing = Filing(
        id="tr_kap_1",
        company_id="tr_kap_x",
        source="kap",
        source_id="1",
        title="t",
        category="accounts",
        filing_type="financial_report",
        filing_date=date(2026, 1, 1),
        issuer_name="x",
        source_url="https://www.kap.org.tr/en/Bildirim/1",
    )
    with pytest.raises(Exception, match="no recognized"):
        extract_kap_structured_financials(
            ["<table><tr><td>nothing here</td></tr></table>"],
            filing,
            source_url=filing.source_url,
            sha256="0" * 64,
        )


def _json_body(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content)


def _company_list() -> list[dict[str, object]]:
    return [
        {
            "mkkMemberOid": "4028e4a240ee866c0140f1fad94e0047",
            "kapMemberTitle": "DENİZ GAYRİMENKUL YATIRIM ORTAKLIĞI A.Ş.",
            "stockCode": "DZGYO",
            "companyCode": "912",
        },
        {
            "mkkMemberOid": "4028e4a2420327a4014209c55161144d",
            "kapMemberTitle": "ACISELSAN ACIPAYAM SELÜLOZ SANAYİ VE TİCARET A.Ş.",
            "stockCode": "ACSEL",
            "companyCode": "1626",
        },
    ]


def _financial_report_row() -> dict[str, object]:
    return {
        "publishDate": "23.07.2026 19:46:35",
        "kapTitle": "DENİZ GAYRİMENKUL YATIRIM ORTAKLIĞI A.Ş.",
        "disclosureClass": "FR",
        "disclosureType": "FR",
        "summary": None,
        "subject": "Finansal Rapor",
        "year": 2026,
        "period": 2,
        "disclosureIndex": 1636286,
        "attachmentCount": 1,
    }


def _other_row() -> dict[str, object]:
    return {
        "publishDate": "26.06.2026 19:26:25",
        "kapTitle": "DENİZ GAYRİMENKUL YATIRIM ORTAKLIĞI A.Ş.",
        "disclosureClass": "ODA",
        "disclosureType": "ODA",
        "summary": "Maddi Duran Varlik Alimi",
        "subject": "Maddi Duran Varlik Alimi",
        "year": None,
        "period": None,
        "disclosureIndex": 1621228,
        "attachmentCount": 1,
    }


def _corporate_action_row() -> dict[str, object]:
    return {
        "publishDate": "27.06.2026 10:15:00",
        "kapTitle": "DENİZ GAYRİMENKUL YATIRIM ORTAKLIĞI A.Ş.",
        "disclosureClass": "ODA",
        "disclosureType": "CA",
        "summary": "Kar Payi Dagitim Islemlerine Iliskin Bildirim",
        "subject": "Kar Payi Dagitim Islemlerine Iliskin Bildirim",
        "year": None,
        "period": None,
        "disclosureIndex": 1621300,
        "attachmentCount": 0,
    }


def _disclosure_detail() -> dict[str, object]:
    return {
        "disclosure": {
            "disclosureBasic": {"disclosureIndex": 1636286},
        },
        "disclosureBody": [_BALANCE_SHEET_TABLE],
        "attachments": [
            {
                "objId": "4028328c9f52dc40019f8f9f330e3ebf",
                "fileName": "DZGYO-Finansal Raporu 30.06.2026 (Final).pdf",
                "fileExtension": "pdf",
            }
        ],
    }


def _java_wrapped_pdf(payload: bytes) -> bytes:
    header = b"\xac\xed\x00\x05ur\x00\x02[B\xac\xf3\x17\xf8\x06\x08T\xe0\x02\x00\x00xp"
    return header + struct.pack(">I", len(payload)) + payload


def _header_cell(label: str, date_text: str) -> str:
    return (
        '<td class="context-header">'
        f'<div class="content-tr">{label}<br/>{date_text}</div>'
        "</td>"
    )


def _name_cell(concept: str) -> str:
    return (
        '<td class="taxonomy-field-name-cell">'
        f'<div class="taxonomy-field-name">{concept}|</div>'
        "</td>"
    )


def _value_cell(raw: str) -> str:
    if not raw:
        return '<td class="taxonomy-context-value"><div></div></td>'
    return f'<td class="taxonomy-context-value"><div title="{raw}">x</div></td>'


def _fact_row(
    concept: str, current: str, previous: str, *, reported: bool = True
) -> str:
    css = "data-input-row presentation-enabled" if reported else "data-input-row"
    values = _value_cell(current) + _value_cell(previous)
    return f'<tr class="{css}">{_name_cell(concept)}{values}</tr>'


_BALANCE_SHEET_TABLE = (
    "<table><tbody>"
    "<tr>"
    + _header_cell("Cari Dönem", "30.06.2026")
    + _header_cell("Önceki Dönem", "31.12.2025")
    + "</tr>"
    '<tr class="abstract-row">'
    + _name_cell("kap-fr_StatementOfFinancialPositionBalanceSheetAbstract")
    + "</tr>"
    + _fact_row("ifrs-full_Assets", "8530658585", "8235745263")
    + _fact_row("ifrs-full_Liabilities", "1883837990", "1556974340")
    + _fact_row("ifrs-full_Equity", "6646820595", "6678770923")
    + _fact_row("ifrs-full_Goodwill", "", "", reported=False)
    + "</tbody></table>"
)
