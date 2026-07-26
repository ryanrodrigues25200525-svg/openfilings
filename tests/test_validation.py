from __future__ import annotations

from datetime import date
from decimal import Decimal

from openfilings.models import (
    FilingFinancials,
    FinancialLineItem,
    FinancialStatement,
    FinancialValue,
    ReportingPeriod,
)
from openfilings.validation import validate_financials, validation_view


def _instant(end_date: date) -> ReportingPeriod:
    return ReportingPeriod(
        id=f"instant-{end_date.isoformat()}",
        end_date=end_date,
        kind="instant",
        fiscal_period="instant",
    )


def _line_item(code: str, name: str, values: dict[date, Decimal]) -> FinancialLineItem:
    return FinancialLineItem(
        code=code,
        name=name,
        concept=f"pdf-label:{code}",
        values=tuple(
            FinancialValue(period=_instant(end_date), value=value)
            for end_date, value in values.items()
        ),
    )


def _balance_sheet(**codes: dict[date, Decimal]) -> FinancialStatement:
    return FinancialStatement(
        statement_type="balance_sheet",
        title="Balance sheet",
        line_items=tuple(
            _line_item(code, code, values) for code, values in codes.items()
        ),
    )


def _financials(*statements: FinancialStatement) -> FilingFinancials:
    return FilingFinancials(
        filing_id="test_filing",
        company_id="test_company",
        source_url="https://example.test/report.pdf",
        statements=statements,
        fact_count=sum(len(item.values) for st in statements for item in st.line_items),
        sha256="a" * 64,
    )


def test_validate_financials_passes_when_the_balance_sheet_ties_out() -> None:
    end_date = date(2025, 12, 31)
    financials = _financials(
        _balance_sheet(
            total_assets={end_date: Decimal("100")},
            total_liabilities={end_date: Decimal("60")},
            total_equity={end_date: Decimal("40")},
        )
    )

    report = validate_financials(financials)
    view = validation_view(report)

    assert view is not None
    assert view["ok"] is True
    assert view["checks_failed"] == 0
    assert view["findings"] == []


def test_validate_financials_flags_a_balance_sheet_that_does_not_tie_out() -> None:
    """This is exactly the failure mode this integration exists for: a
    PDF-heuristic extractor can produce a plausible-looking but wrong
    number by matching the wrong row - confirmed live this session on a
    Colombian filing where current_assets + noncurrent_assets came from a
    standalone statement while total_assets came from a consolidated one,
    both individually correct but mutually inconsistent."""
    end_date = date(2025, 12, 31)
    financials = _financials(
        _balance_sheet(
            total_assets={end_date: Decimal("37636229")},
            total_liabilities={end_date: Decimal("18073014")},
            total_equity={end_date: Decimal("19563215")},
            current_assets={end_date: Decimal("703092")},
            noncurrent_assets={end_date: Decimal("13125747")},
        )
    )

    report = validate_financials(financials)
    view = validation_view(report)

    assert view is not None
    assert view["ok"] is False
    finding = next(
        f for f in view["findings"] if f["rule_id"] == "FOOT.bs.total_assets"
    )
    assert finding["expected"] == 13828839.0
    assert finding["actual"] == 37636229.0


def test_validate_financials_ignores_income_statement_presentations() -> None:
    """Tagged cost-of-sales and gross-profit facts are not universally
    comparable subtotals, so they must not generate a false warning."""
    end_date = date(2025, 12, 31)
    income_statement = FinancialStatement(
        statement_type="income_statement",
        title="Income statement",
        line_items=(
            _line_item("revenue", "Revenue", {end_date: Decimal("1000")}),
            _line_item(
                "cost_of_revenue", "Cost of revenue", {end_date: Decimal("-400")}
            ),
            _line_item("gross_profit", "Gross profit", {end_date: Decimal("600")}),
            _line_item(
                "operating_income_loss",
                "Operating income",
                {end_date: Decimal("450")},
            ),
            _line_item(
                "profit_before_tax", "Profit before tax", {end_date: Decimal("300")}
            ),
            _line_item("net_income_loss", "Net income", {end_date: Decimal("250")}),
        ),
    )
    financials = _financials(income_statement)

    report = validate_financials(financials)
    view = validation_view(report)

    assert view is None


def test_validate_financials_ignores_cash_movement_without_fx_reconciliation() -> None:
    end_date = date(2025, 12, 31)
    cash_flow = FinancialStatement(
        statement_type="cash_flow_statement",
        title="Cash flow statement",
        line_items=(
            _line_item(
                "operating_cash_flow", "Operating cash flow", {end_date: Decimal("100")}
            ),
            _line_item(
                "investing_cash_flow", "Investing cash flow", {end_date: Decimal("-60")}
            ),
            _line_item(
                "financing_cash_flow", "Financing cash flow", {end_date: Decimal("-20")}
            ),
            # The remaining ten is a legitimate FX/restricted-cash movement.
            _line_item(
                "net_change_in_cash", "Net change in cash", {end_date: Decimal("30")}
            ),
        ),
    )

    view = validation_view(validate_financials(_financials(cash_flow)))

    assert view is None


def test_validate_financials_returns_none_when_nothing_is_mappable() -> None:
    end_date = date(2025, 12, 31)
    financials = _financials(
        FinancialStatement(
            statement_type="income_statement",
            title="Income statement",
            line_items=(
                _line_item("basic_eps", "Basic EPS", {end_date: Decimal("1.23")}),
            ),
        )
    )

    report = validate_financials(financials)

    assert report is None
    assert validation_view(report) is None
