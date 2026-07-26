"""Deterministic accounting-identity checks via the finvariant library.

finvariant verifies that a set of financial statements is internally
consistent (assets = liabilities + equity, subtotals foot, cash ties out) -
exactly the kind of arithmetic error a PDF-heuristic extractor can produce
when it silently matches the wrong row. It never parses or builds
statements, only checks them, so this module's only job is mapping
OpenFilings' FinancialStatement/FinancialLineItem schema into finvariant's
canonical field names.

Only whole-statement subtotals are mapped, never sub-line items (cash,
receivables, inventory, ...): finvariant treats a partially-supplied
section as "not footing" rather than skipping it, and OpenFilings' PDF
extraction rarely captures every component of a section - supplying a
partial breakdown would produce a false failure, not a useful check.
"""

from __future__ import annotations

import finvariant as fv

from openfilings.models import FilingFinancials, FinancialStatement

# OpenFilings line-item code -> (finvariant field, take absolute value).
# finvariant's income-statement expenses (cogs, tax) are positive
# magnitudes; OpenFilings' PDF-heuristic values often carry the source's
# own sign (frequently negative, shown in parentheses in the filing).
#
# Only revenue/cogs/gross_profit are mapped, deliberately. finvariant's
# operating_income and pretax_income checks need operating_expenses/
# other_income/interest_expense to compute a correct expected value; when
# those are absent (which OpenFilings' headline-figure extraction always
# is - it has no such granularity), finvariant treats the missing minus-
# term as zero rather than skipping the check entirely, so it would
# compare operating_income_loss straight against gross_profit and
# profit_before_tax straight against operating_income - which are
# genuinely different figures for any real company, producing a false
# failure on every single filing rather than flagging a real bug.
# gross_profit is the one check where OpenFilings reliably has both
# inputs (revenue and cost_of_revenue) whenever it has the output.
_INCOME_STATEMENT_FIELDS: dict[str, tuple[str, bool]] = {
    "revenue": ("revenue", False),
    "cost_of_revenue": ("cogs", True),
    "gross_profit": ("gross_profit", False),
}
_BALANCE_SHEET_FIELDS: dict[str, tuple[str, bool]] = {
    "current_assets": ("total_current_assets", False),
    "noncurrent_assets": ("total_non_current_assets", False),
    "total_assets": ("total_assets", False),
    "current_liabilities": ("total_current_liabilities", False),
    "noncurrent_liabilities": ("total_non_current_liabilities", False),
    "total_liabilities": ("total_liabilities", False),
    "total_equity": ("total_equity", False),
}
_CASH_FLOW_FIELDS: dict[str, tuple[str, bool]] = {
    "operating_cash_flow": ("cfo", False),
    "investing_cash_flow": ("cfi", False),
    "financing_cash_flow": ("cff", False),
    "net_change_in_cash": ("net_change_in_cash", False),
}
_FIELDS_BY_STATEMENT_TYPE = {
    "income_statement": _INCOME_STATEMENT_FIELDS,
    "balance_sheet": _BALANCE_SHEET_FIELDS,
    "cash_flow_statement": _CASH_FLOW_FIELDS,
}


def _statement_periods(
    statement: FinancialStatement, field_map: dict[str, tuple[str, bool]]
) -> dict[str, dict[str, float]]:
    by_period: dict[str, dict[str, float]] = {}
    for item in statement.line_items:
        mapping = field_map.get(item.code)
        if mapping is None:
            continue
        field_name, use_abs = mapping
        for value in item.values:
            period_key = value.period.end_date.isoformat()
            amount = abs(value.value) if use_abs else value.value
            by_period.setdefault(period_key, {})[field_name] = float(amount)
    return by_period


def _merge(
    target: dict[str, dict[str, float]], source: dict[str, dict[str, float]]
) -> None:
    for period, fields in source.items():
        target.setdefault(period, {}).update(fields)


def to_finvariant_statements(financials: FilingFinancials) -> fv.Statements | None:
    """Map a FilingFinancials into finvariant's canonical schema.

    Returns None when there's nothing worth checking (no statement of a
    type finvariant understands, or no periods at all).
    """
    income_statement: dict[str, dict[str, float]] = {}
    balance_sheet: dict[str, dict[str, float]] = {}
    cash_flow: dict[str, dict[str, float]] = {}
    for statement in financials.statements:
        field_map = _FIELDS_BY_STATEMENT_TYPE.get(statement.statement_type)
        if field_map is None:
            continue
        periods = _statement_periods(statement, field_map)
        if statement.statement_type == "income_statement":
            _merge(income_statement, periods)
        elif statement.statement_type == "balance_sheet":
            _merge(balance_sheet, periods)
        elif statement.statement_type == "cash_flow_statement":
            _merge(cash_flow, periods)

    all_periods = sorted(
        set(income_statement) | set(balance_sheet) | set(cash_flow), reverse=True
    )
    if not all_periods:
        return None
    return fv.Statements(
        periods=all_periods,
        income_statement=income_statement,
        balance_sheet=balance_sheet,
        cash_flow=cash_flow,
    )


def validate_financials(financials: FilingFinancials) -> fv.AuditReport | None:
    """Run finvariant's deterministic integrity checks, or None if there
    was nothing mappable to check."""
    statements = to_finvariant_statements(financials)
    if statements is None:
        return None
    return fv.check(statements)


def validation_view(report: fv.AuditReport | None) -> dict[str, object] | None:
    """A compact JSON view of an AuditReport for an MCP response."""
    if report is None:
        return None
    return {
        "ok": report.ok,
        "checks_passed": report.n_passed,
        "checks_failed": report.n_failed,
        "checks_skipped": report.n_skipped,
        "findings": [
            {
                "rule_id": finding.rule_id,
                "description": finding.description,
                "period": finding.period,
                "expected": finding.expected,
                "actual": finding.actual,
                "difference": finding.difference,
            }
            for finding in report.findings
            if finding.status == "fail"
        ],
    }
