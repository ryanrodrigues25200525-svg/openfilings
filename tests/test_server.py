from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from openfilings import server
from openfilings.domain import FilingDocument
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
