from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from openfilings import server
from openfilings.adapters.base import SourceDocument
from openfilings.domain import FilingDocument
from openfilings.exceptions import FinancialsUnavailableError
from openfilings.models import (
    Company,
    Filing,
    FilingContent,
    FilingFinancials,
    FinancialLineItem,
    FinancialStatement,
    FinancialValue,
    ReportingPeriod,
)
from openfilings.resources import FilingResource
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache


class _FakeService:
    def __init__(self) -> None:
        self.company = Company(
            id="mx_bmv_6024",
            source_id="6024",
            name="AMERICA MOVIL, S.A.B. DE C.V.",
            sources=("bmv",),
            market="MX",
            country_code="MX",
            ticker="AMX",
            status="active listed equity issuer",
            source_url="https://www.bmv.com.mx/AMX",
        )
        self.filing = Filing(
            id="mx_bmv_filing_1552461",
            company_id=self.company.id,
            source="bmv",
            source_id="1552461",
            title="2025 Annual Report",
            category="accounts",
            filing_type="annual",
            filing_date=date(2026, 4, 28),
            period_end=date(2025, 12, 31),
            document_id="https://www.bmv.com.mx/report.pdf",
            media_type="application/pdf",
            issuer_name=self.company.name,
            pdf_available=True,
            source_url="https://www.bmv.com.mx/report.pdf",
        )
        repeated_risk = "Climate regulation and competition affect operations. " * 300
        markdown = (
            "# 2025 Annual Report\n\n"
            "Company overview.\n\n"
            "## Risk Factors\n\n"
            f"{repeated_risk}\n\n"
            "## Financial Statements\n\n"
            "Revenue increased while operating costs remained controlled.\n"
        )
        self.content = FilingContent(
            filing_id=self.filing.id,
            markdown=markdown,
            source_url=self.filing.source_url,
            sha256="a" * 64,
        )
        self.financials = _financials(self.filing)

    async def __aenter__(self) -> _FakeService:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def search_companies(self, *_: object, **__: object) -> list[Company]:
        return [self.company]

    async def list_filings(self, *_: object, **__: object) -> list[Filing]:
        return [self.filing]

    async def get_filing_markdown(self, *_: object, **__: object) -> FilingContent:
        return self.content

    async def get_filing_document(self, *_: object, **__: object) -> FilingDocument:
        return FilingDocument.from_content(self.content)

    async def get_filing_financials(self, *_: object, **__: object) -> FilingFinancials:
        return self.financials

    async def import_sedar_filing(self, *_: object, **__: object) -> FilingResource:
        return FilingResource(self.filing, self)  # type: ignore[arg-type]


@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> _FakeService:
    service = _FakeService()
    monkeypatch.setattr(
        server.OpenFilingsService,
        "from_settings",
        staticmethod(lambda: service),
    )
    return service


@pytest.mark.asyncio
async def test_metadata_tools_return_compact_guided_responses(
    fake_service: _FakeService,
) -> None:
    companies = await server.companies_search("AMX", source="bmv")
    filings = await server.filings_list(fake_service.company.id, source="bmv")

    assert companies["success"] is True
    assert companies["data"]["companies"] == [
        {
            "id": "mx_bmv_6024",
            "name": "AMERICA MOVIL, S.A.B. DE C.V.",
            "market": "MX",
            "country_code": "MX",
            "ticker": "AMX",
            "sources": ["bmv"],
            "status": "active listed equity issuer",
            "source_url": "https://www.bmv.com.mx/AMX",
        }
    ]
    assert "next_steps" in companies
    assert filings["data"]["filings"][0]["period_end"] == "2025-12-31"
    assert "document_id" not in filings["data"]["filings"][0]


@pytest.mark.asyncio
async def test_mcp_registers_progressive_disclosure_tools() -> None:
    tools = await server.mcp.list_tools()

    assert {tool.name for tool in tools} == {
        "companies_search",
        "filings_list",
        "disclosures_search",
        "company_facts",
        "major_holders_list",
        "major_holders_search",
        "sedar_filing_import",
        "filing_outline",
        "filing_sections",
        "filing_read",
        "filing_search",
        "filing_markdown",
        "filing_financials",
    }


@pytest.mark.asyncio
async def test_sedar_import_returns_compact_metadata(
    fake_service: _FakeService,
) -> None:
    response = await server.sedar_filing_import(
        "ca_sedar_tsx_SHOP",
        (
            "https://www.sedarplus.ca/csa-party/viewInstance/view.html"
            "?id=public-document"
        ),
        "2025 Annual Report",
        date(2026, 3, 12),
        period_end=date(2025, 12, 31),
    )

    assert response["success"] is True
    assert response["data"]["filing"]["id"] == fake_service.filing.id
    assert "document_id" not in response["data"]["filing"]


@pytest.mark.asyncio
async def test_filing_markdown_is_bounded_and_paginated(
    fake_service: _FakeService,
) -> None:
    first = await server.filing_markdown(
        fake_service.filing.id,
        max_chars=2_000,
    )
    second = await server.filing_markdown(
        fake_service.filing.id,
        offset=first["data"]["next_offset"],
        max_chars=2_000,
    )

    assert len(first["data"]["markdown"]) <= 2_000
    assert first["data"]["truncated"] is True
    assert first["data"]["next_offset"] == 2_000
    assert first["data"]["total_chars"] == len(fake_service.content.markdown)
    assert second["data"]["offset"] == 2_000


@pytest.mark.asyncio
async def test_outline_read_and_search_use_progressive_disclosure(
    fake_service: _FakeService,
) -> None:
    outline = await server.filing_outline(fake_service.filing.id)
    read = await server.filing_read(
        fake_service.filing.id,
        section="risk factors",
        max_chars=1_500,
    )
    search = await server.filing_search(
        fake_service.filing.id,
        query="operating costs",
        limit=3,
        snippet_chars=500,
    )

    assert outline["data"]["sections"][1]["title"] == "Risk Factors"
    assert "markdown" not in outline["data"]["sections"][1]
    assert len(read["data"]["markdown"]) <= 1_500
    assert read["data"]["title"] == "Risk Factors"
    assert search["data"]["results"][0]["title"] == "Financial Statements"
    assert len(search["data"]["results"][0]["snippet"]) <= 500


@pytest.mark.asyncio
async def test_financials_support_statement_period_and_row_filters(
    fake_service: _FakeService,
) -> None:
    response = await server.filing_financials(
        fake_service.filing.id,
        statements=["income_statement"],
        periods=1,
        max_line_items=1,
    )

    statement = response["data"]["statements"][0]
    assert [item["statement_type"] for item in response["data"]["statements"]] == [
        "income_statement"
    ]
    assert statement["periods"] == ["FY 2025 2025-12-31"]
    assert len(statement["line_items"]) == 1
    assert statement["truncated"] is True


@pytest.mark.asyncio
async def test_financials_failure_points_to_manual_extraction_fallback(
    fake_service: _FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When structured extraction fails, the calling agent should be told
    how to read the filing's own (already-converted) text directly instead
    of just seeing a bare error - the document itself is still readable."""

    async def raise_unavailable(*_: object, **__: object) -> FilingFinancials:
        raise FinancialsUnavailableError(
            "The PDF contains no high-confidence aligned statement text."
        )

    monkeypatch.setattr(fake_service, "get_filing_financials", raise_unavailable)

    response = await server.filing_financials(fake_service.filing.id)

    assert response["success"] is False
    assert response["error_code"] == "FINANCIALSUNAVAILABLEERROR"
    suggestions = " ".join(response["suggestions"])
    assert "filing_search" in suggestions
    assert "filing_markdown" in suggestions


@pytest.mark.asyncio
async def test_filing_financials_extracts_a_real_pdf_end_to_end(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other MCP test in this file mocks get_filing_financials to
    return a hand-built FilingFinancials object, so none of them exercise
    the real PDF extraction pipeline (heading detection, label matching,
    the RapidFuzz fallback) through the actual tool the way an MCP client
    would call it. This is the gap that let a real regression through 176
    passing unit tests: "Other non-current liabilities" (a genuine
    Keppel sub-item) fuzzy-matched the same code as its own parent
    section and silently overwrote the real subtotal - only caught by
    manually calling filing_financials() against a live filing. This test
    reproduces that exact shape with a real, minimal PDF (not a text
    fixture), routed through the real OpenFilingsService and the real
    MCP tool function, so a regression here fails a test instead of
    requiring a live filing to notice.
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    lines = (
        "Balance Sheets",
        "as at 31 December 2025",
        "GROUP",
        "COMPANY",
        "Note",
        "2025",
        "$'000",
        "2024",
        "$'000",
        "2025",
        "$'000",
        "2024",
        "$'000",
        "Non-current liabilities",
        "Term loans",
        "25",
        "9,409,036",
        "10,509,001",
        "8,493,628",
        "8,161,900",
        "Other non-current liabilities",
        "23",
        "120,968",
        "332,819",
        "28,156",
        "28,156",
        "10,122,923",
        "11,461,649",
        "8,647,781",
        "8,240,799",
        "Net assets",
        "11,186,180",
        "11,425,661",
        "7,945,822",
        "8,058,123",
    )
    y = 50
    for line in lines:
        page.insert_text((50, y), line, fontsize=10)
        y += 15
    pdf_bytes = doc.tobytes()
    doc.close()

    filing = Filing(
        id="sg_sgx_keppel_like",
        company_id="sg_sgx_1L01",
        source="sgx",
        source_id="keppel_like",
        title="2025 Annual Report",
        category="accounts",
        filing_type="annual",
        filing_date=date(2026, 3, 1),
        period_end=date(2025, 12, 31),
        document_id="https://example.test/keppel-like.pdf",
        media_type="application/pdf",
        issuer_name="Example Ltd",
        pdf_available=True,
        source_url="https://example.test/keppel-like.pdf",
    )
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    cache.put_filings([filing])
    service = OpenFilingsService(cache)

    async def download(_filing: Filing) -> SourceDocument:
        return SourceDocument(
            data=pdf_bytes,
            media_type="application/pdf",
            source_url=filing.source_url,
        )

    monkeypatch.setattr(service, "_download_document", download)
    monkeypatch.setattr(
        server.OpenFilingsService, "from_settings", staticmethod(lambda: service)
    )

    response = await server.filing_financials(filing.id)
    cache.close()

    assert response["success"] is True
    balance = next(
        item
        for item in response["data"]["statements"]
        if item["statement_type"] == "balance_sheet"
    )
    values_by_code = {item["code"]: item["values"] for item in balance["line_items"]}
    noncurrent_liabilities = values_by_code["noncurrent_liabilities"]
    assert noncurrent_liabilities["instant 2025-12-31"] == "10122923000"
    assert noncurrent_liabilities["instant 2024-12-31"] == "11461649000"


def _financials(filing: Filing) -> FilingFinancials:
    period_2025 = ReportingPeriod(
        id="fy2025",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        kind="duration",
        fiscal_period="FY 2025",
    )
    period_2024 = ReportingPeriod(
        id="fy2024",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        kind="duration",
        fiscal_period="FY 2024",
    )
    values = (
        FinancialValue(period=period_2025, value=Decimal("100"), unit="MXN"),
        FinancialValue(period=period_2024, value=Decimal("90"), unit="MXN"),
    )
    income = FinancialStatement(
        statement_type="income_statement",
        title="Income Statement",
        currency="MXN",
        line_items=(
            FinancialLineItem(
                code="revenue",
                name="Revenue",
                concept="ifrs-full:Revenue",
                values=values,
            ),
            FinancialLineItem(
                code="profit",
                name="Profit",
                concept="ifrs-full:ProfitLoss",
                values=values,
            ),
        ),
    )
    balance = FinancialStatement(
        statement_type="balance_sheet",
        title="Balance Sheet",
        currency="MXN",
        line_items=(),
    )
    return FilingFinancials(
        filing_id=filing.id,
        company_id=filing.company_id,
        source_url=filing.source_url,
        statements=(income, balance),
        fact_count=4,
        sha256="b" * 64,
    )
