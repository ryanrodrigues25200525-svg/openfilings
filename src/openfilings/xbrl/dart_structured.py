"""Structured financial statements from DART's fnlttSinglAcntAll.json.

DART's ``account_id`` field carries the filer's "XBRL 표준계정ID" (XBRL
standard account ID) - for companies using the IFRS-XBRL editor this is the
literal IFRS taxonomy concept, underscore-joined with its namespace (e.g.
``ifrs-full_Assets``, ``ifrs-full_Revenue``), so it maps onto the concepts
already recognized in xbrl/mappings.py without any DART-specific aliasing,
the same way NSE's tagged filings do even though this is a plain JSON
endpoint rather than an XBRL document. Rows without a standard ID (company-
specific extension accounts) don't match a definition and are skipped.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from openfilings.exceptions import FinancialsUnavailableError
from openfilings.models import (
    Filing,
    FilingFinancials,
    FinancialLineItem,
    FinancialStatement,
    FinancialValue,
    ReportingPeriod,
    StatementType,
)
from openfilings.xbrl.mappings import definition_for_concept

_STATEMENT_TITLES: dict[StatementType, str] = {
    "income_statement": "Income statement",
    "balance_sheet": "Balance sheet",
    "cash_flow_statement": "Cash flow statement",
    "comprehensive_income": "Statement of comprehensive income",
    "changes_in_equity": "Statement of changes in equity",
}
_SJ_DIV_STATEMENTS: dict[str, StatementType] = {
    "BS": "balance_sheet",
    "IS": "income_statement",
    "CIS": "comprehensive_income",
    "CF": "cash_flow_statement",
    "SCE": "changes_in_equity",
}


def extract_dart_structured_financials(
    rows: list[dict[str, object]],
    filing: Filing,
    *,
    period: ReportingPeriod,
    source_url: str,
    sha256: str,
) -> FilingFinancials:
    """Build normalized statements from one fnlttSinglAcntAll.json response."""

    items_by_statement: dict[StatementType, dict[str, FinancialLineItem]] = {}
    for row in rows:
        statement_type = _SJ_DIV_STATEMENTS.get(str(row.get("sj_div", "")).strip())
        if statement_type is None:
            continue
        definition = definition_for_concept(_concept(row))
        if definition is None or definition.statement_type != statement_type:
            continue
        value = _decimal(row.get("thstrm_amount"))
        if value is None:
            continue
        items = items_by_statement.setdefault(statement_type, {})
        if definition.code in items:
            continue
        item_period = (
            ReportingPeriod(
                id=f"dart-instant-{period.end_date.isoformat()}",
                end_date=period.end_date,
                kind="instant",
                fiscal_period="instant",
            )
            if statement_type == "balance_sheet"
            else period
        )
        items[definition.code] = FinancialLineItem(
            code=definition.code,
            name=definition.name,
            concept=_concept(row),
            values=(
                FinancialValue(
                    period=item_period,
                    value=value,
                    unit=str(row.get("currency", "")).strip() or "KRW",
                    decimals="0",
                    provenance="regulated_structured_data",
                    confidence=95,
                ),
            ),
        )

    statements = tuple(
        FinancialStatement(
            statement_type=statement_type,
            title=_STATEMENT_TITLES[statement_type],
            currency=_statement_currency(items.values()),
            line_items=tuple(items.values()),
        )
        for statement_type, items in items_by_statement.items()
        if items
    )
    if not statements:
        raise FinancialsUnavailableError(
            "DART's financial-statement dataset has no recognized IFRS "
            "accounts for this company and period."
        )
    return FilingFinancials(
        filing_id=filing.id,
        company_id=filing.company_id,
        source_url=source_url,
        extraction_method="dart-fnlttsinglacntall",
        statements=statements,
        fact_count=sum(
            len(item.values)
            for statement in statements
            for item in statement.line_items
        ),
        taxonomy_namespaces=("ifrs-full",),
        sha256=sha256,
    )


def _concept(row: dict[str, object]) -> str:
    account_id = str(row.get("account_id", "")).strip()
    prefix, sep, local = account_id.partition("_")
    return f"{prefix}:{local}" if sep else account_id


def _statement_currency(items: Iterable[FinancialLineItem]) -> str | None:
    for item in items:
        for value in item.values:
            if value.unit:
                return value.unit
    return None


def _decimal(value: object) -> Decimal | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None
