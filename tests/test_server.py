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
    CompanyFacts,
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

    async def get_company_facts(self, *_: object, **__: object) -> CompanyFacts:
        return CompanyFacts(
            company_id=self.company.id,
            statements=self.financials.statements,
            filing_ids=(self.filing.id,),
        )

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


@pytest.fixture
def real_html_service(
    tmp_path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> tuple[OpenFilingsService, Filing]:
    filing = Filing(
        id="mx_bmv_filing_real_html",
        company_id="mx_bmv_real_html",
        source="bmv",
        source_id="real_html",
        title="2025 Annual Report",
        category="accounts",
        filing_type="annual",
        filing_date=date(2026, 4, 28),
        period_end=date(2025, 12, 31),
        document_id="https://example.test/annual-report.html",
        media_type="text/html",
        issuer_name="Example Industrials, S.A.B. de C.V.",
        source_url="https://example.test/annual-report.html",
    )
    html = (
        b"<!doctype html><html><body>"
        b"<h1>Annual Report</h1>"
        b"<p>Company overview and strategy for long-term growth.</p>"
        b"<h2>Risk Factors</h2>"
        b"<p>Supply chain disruption and currency volatility may affect operations. "
        + (b"Climate regulation may increase compliance costs. " * 8)
        + b"</p>"
        b"<h2>Financial Results</h2>"
        b"<p>Revenue increased by 12 percent while operating costs remained "
        b"controlled. Net income improved during the year.</p>"
        b"<script>confidential_tracking_payload</script>"
        b"</body></html>"
    )
    cache = SQLiteCache(tmp_path / "real-html-cache.sqlite3")
    request.addfinalizer(cache.close)
    cache.put_filings([filing])
    service = OpenFilingsService(cache)

    async def download(_filing: Filing) -> SourceDocument:
        assert _filing == filing
        return SourceDocument(
            data=html,
            media_type="text/html",
            source_url=filing.source_url,
        )

    monkeypatch.setattr(service, "_download_document", download)
    monkeypatch.setattr(
        server.OpenFilingsService,
        "from_settings",
        staticmethod(lambda: service),
    )
    return service, filing


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
        "insider_dealings_list",
        "sedar_filing_import",
        "filing_outline",
        "filing_sections",
        "filing_read",
        "filing_search",
        "filing_markdown",
        "filing_financials",
        "data_quality_report",
        "financials_query",
        "historical_backfill",
        "historical_facts_query",
        "company_research_brief",
        "companies_compare",
        "filings_diff",
        "watchlist_check",
    }


@pytest.mark.asyncio
async def test_research_mcp_tools_return_bounded_structured_responses(
    fake_service: _FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    quality = await server.data_quality_report(fake_service.filing.id)
    queried = await server.financials_query(
        fake_service.company.id,
        codes=["revenue"],
    )
    brief = await server.company_research_brief(fake_service.company.id)
    watchlist = await server.watchlist_check(
        [fake_service.company.id], since=date(2026, 1, 1)
    )

    async def comparable_facts(company_id: str, **_: object) -> CompanyFacts:
        return CompanyFacts(
            company_id=company_id,
            statements=fake_service.financials.statements,
            filing_ids=(fake_service.filing.id,),
        )

    monkeypatch.setattr(fake_service, "get_company_facts", comparable_facts)
    comparison = await server.companies_compare(
        ["uk_fca_GB00AAA00001", "nl_esef_12345"], code="revenue"
    )

    original = fake_service.financials
    changed_value = (
        original.statements[0]
        .line_items[0]
        .values[0]
        .model_copy(update={"value": Decimal("101")})
    )
    changed_item = (
        original.statements[0]
        .line_items[0]
        .model_copy(
            update={
                "values": (
                    changed_value,
                    *original.statements[0].line_items[0].values[1:],
                )
            }
        )
    )
    changed_statement = original.statements[0].model_copy(
        update={
            "line_items": (
                changed_item,
                *original.statements[0].line_items[1:],
            )
        }
    )
    changed_financials = original.model_copy(
        update={"statements": (changed_statement, *original.statements[1:])}
    )

    async def different_financials(filing_id: str, **_: object) -> FilingFinancials:
        return original if filing_id == "first" else changed_financials

    monkeypatch.setattr(fake_service, "get_filing_financials", different_financials)
    difference = await server.filings_diff("first", "second")

    assert quality["success"] is True
    assert quality["data"]["provenance_counts"] == {"tagged_xbrl": 4}
    assert queried["data"]["facts"][0]["code"] == "revenue"
    assert brief["data"]["financials"]["company_id"] == fake_service.company.id
    assert comparison["data"]["companies"][0]["values"][0]["period_end"] == "2025-12-31"
    assert difference["data"]["changes"][0]["first"]["value"] == "100"
    assert difference["data"]["changes"][0]["second"]["value"] == "101"
    assert watchlist["data"]["updates"][fake_service.company.id][0]["id"] == (
        fake_service.filing.id
    )


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
async def test_filing_markdown_converts_real_html_end_to_end(
    real_html_service: tuple[OpenFilingsService, Filing],
) -> None:
    """Mocked FilingContent cannot catch failures in HTML cleanup, Markdown
    conversion, quality assessment, or the service-added provenance header.
    This calls the real MCP tool through all of those stages, substituting
    only the network download with representative filing HTML."""
    _, filing = real_html_service

    response = await server.filing_markdown(filing.id)

    assert response["success"] is True
    assert response["data"]["extraction_method"] == "markdownify"
    assert response["data"]["quality"]["status"] == "good"
    assert "# Annual Report" in response["data"]["markdown"]
    assert "## Risk Factors" in response["data"]["markdown"]
    assert "confidential_tracking_payload" not in response["data"]["markdown"]


@pytest.mark.asyncio
async def test_filing_search_ranks_sections_from_real_html_pipeline(
    real_html_service: tuple[OpenFilingsService, Filing],
) -> None:
    """A hand-built FilingDocument bypasses both HTML heading conversion and
    section construction, so it cannot reveal when real converted headings
    stop being searchable. This test builds the searchable document through
    the real service and exercises the MCP ranking and excerpt path."""
    _, filing = real_html_service

    response = await server.filing_search(
        filing.id,
        query="revenue operating costs",
        limit=2,
        snippet_chars=200,
    )

    assert response["success"] is True
    assert response["data"]["results"][0]["title"] == "Financial Results"
    assert response["data"]["results"][0]["matched_terms"] == [
        "costs",
        "operating",
        "revenue",
    ]
    assert (
        "Revenue increased by 12 percent" in response["data"]["results"][0]["snippet"]
    )


@pytest.mark.asyncio
async def test_filing_read_returns_section_from_real_html_pipeline(
    real_html_service: tuple[OpenFilingsService, Filing],
) -> None:
    """The mocked read test starts after document parsing has already
    succeeded. Using real HTML here verifies that conversion produces a
    named section which the actual MCP read tool can resolve and paginate."""
    _, filing = real_html_service

    response = await server.filing_read(
        filing.id,
        section="risk factors",
        max_chars=300,
    )

    assert response["success"] is True
    assert response["data"]["title"] == "Risk Factors"
    assert response["data"]["markdown"].startswith("## Risk Factors")
    assert "Supply chain disruption" in response["data"]["markdown"]
    assert response["data"]["truncated"] is True
    assert response["data"]["next_offset"] == 300


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
async def test_financials_warns_when_the_accounting_identity_does_not_hold(
    fake_service: _FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """extraction can "succeed" (return a number for every requested line
    item) while still being wrong - a PDF-heuristic extractor matching the
    wrong row produces a plausible-looking figure, not an error. The
    deterministic accounting-identity check (finvariant) is the signal
    that tells the calling agent to verify before trusting a result that
    technically returned data."""
    period = ReportingPeriod(
        id="instant2025", end_date=date(2025, 12, 31), kind="instant"
    )
    inconsistent_balance_sheet = FinancialStatement(
        statement_type="balance_sheet",
        title="Balance Sheet",
        line_items=(
            FinancialLineItem(
                code="total_assets",
                name="Total assets",
                concept="pdf-label:total-assets",
                values=(FinancialValue(period=period, value=Decimal("100")),),
            ),
            FinancialLineItem(
                code="total_liabilities",
                name="Total liabilities",
                concept="pdf-label:total-liabilities",
                values=(FinancialValue(period=period, value=Decimal("60")),),
            ),
            FinancialLineItem(
                code="total_equity",
                name="Total equity",
                concept="pdf-label:total-equity",
                values=(FinancialValue(period=period, value=Decimal("30")),),
            ),
        ),
    )
    broken = fake_service.financials.model_copy(
        update={"statements": (inconsistent_balance_sheet,)}
    )

    async def get_broken(*_: object, **__: object) -> FilingFinancials:
        return broken

    monkeypatch.setattr(fake_service, "get_filing_financials", get_broken)

    response = await server.filing_financials(fake_service.filing.id)

    assert response["success"] is True
    validation = response["data"]["validation"]
    assert validation["ok"] is False
    assert validation["findings"]
    assert any("accounting-identity check" in step for step in response["next_steps"])


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
