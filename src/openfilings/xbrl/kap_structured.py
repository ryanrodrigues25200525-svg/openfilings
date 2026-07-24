"""Structured financial statements from KAP's rendered XBRL viewer tables.

KAP's "Finansal Rapor" disclosures don't expose a downloadable raw XBRL
instance - instead, each statement is pre-rendered as an HTML viewer table
(one per statement) whose row carries the filer's literal tagged concept
(``ifrs-full_Assets``, ``kap-fr_...``) next to its reported value. That
concept maps onto xbrl/mappings.py the same way DART's JSON rows do; only
the source shape (HTML table instead of JSON) differs. The statement-of-
changes-in-equity table uses a different rowspan-based period layout this
parser doesn't handle, so its rows simply match no recognized concept and
contribute nothing.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, Tag

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
_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_VALUE_CELL_CLASS = re.compile(r"^taxonomy-context-value")
# Turkish issuers report to KAP in Turkish Lira by law (Uniform Chart of
# Accounts); the presentation-currency label in the source HTML is "TL".
_CURRENCY = "TRY"


def extract_kap_structured_financials(
    bodies: list[str],
    filing: Filing,
    *,
    source_url: str,
    sha256: str,
) -> FilingFinancials:
    """Build normalized statements from a KAP "Finansal Rapor" disclosure's
    per-statement viewer tables."""

    items_by_statement: dict[StatementType, dict[str, FinancialLineItem]] = {}
    for body in bodies:
        _extract_one_table(body, items_by_statement)

    statements = tuple(
        FinancialStatement(
            statement_type=statement_type,
            title=_STATEMENT_TITLES[statement_type],
            currency=_CURRENCY,
            line_items=tuple(items.values()),
        )
        for statement_type, items in items_by_statement.items()
        if items
    )
    if not statements:
        raise FinancialsUnavailableError(
            "KAP's financial report has no recognized IFRS statement facts."
        )
    return FilingFinancials(
        filing_id=filing.id,
        company_id=filing.company_id,
        source_url=source_url,
        extraction_method="kap-finansal-rapor-viewer",
        statements=statements,
        fact_count=sum(
            len(item.values)
            for statement in statements
            for item in statement.line_items
        ),
        taxonomy_namespaces=("ifrs-full", "kap-fr"),
        sha256=sha256,
    )


def _extract_one_table(
    body: str, items_by_statement: dict[StatementType, dict[str, FinancialLineItem]]
) -> None:
    soup = BeautifulSoup(body, "html.parser")
    rows = soup.find_all("tr")
    if not rows:
        return
    periods = _periods(rows)
    if not periods:
        return

    for row in rows:
        if "data-input-row" not in (row.get("class") or []):
            continue
        concept = _concept(row)
        if concept is None:
            continue
        definition = definition_for_concept(concept)
        if definition is None:
            continue
        value_cells = row.find_all("td", class_=_VALUE_CELL_CLASS)
        values = tuple(
            FinancialValue(period=period, value=amount, unit=_CURRENCY, decimals="0")
            for period, cell in zip(periods, value_cells, strict=False)
            if (amount := _decimal(cell)) is not None
        )
        if not values:
            continue
        bucket = items_by_statement.setdefault(definition.statement_type, {})
        if definition.code in bucket:
            continue
        bucket[definition.code] = FinancialLineItem(
            code=definition.code,
            name=definition.name,
            concept=concept,
            values=values,
        )


def _periods(rows: list[Tag]) -> list[ReportingPeriod]:
    for row in rows:
        headers = row.find_all("td", class_="context-header")
        if not headers:
            continue
        periods = [
            period
            for header in headers
            if (period := _period_from_header(header)) is not None
        ]
        if periods:
            return periods
    return []


def _period_from_header(header: Tag) -> ReportingPeriod | None:
    label_div = header.find("div", class_="content-tr")
    if label_div is None:
        return None
    text = label_div.get_text(separator="|").strip()
    label, _, rest = text.partition("|")
    if label.strip() not in {"Cari Dönem", "Önceki Dönem"}:
        return None
    dates = [_date(match) for match in _DATE.finditer(rest) if _date(match) is not None]
    if not dates:
        return None
    end_date = dates[-1]
    if len(dates) >= 2:
        start_date = dates[0]
        return ReportingPeriod(
            id=f"kap-duration-{start_date.isoformat()}-{end_date.isoformat()}",
            start_date=start_date,
            end_date=end_date,
            kind="duration",
            fiscal_period="cumulative",
        )
    return ReportingPeriod(
        id=f"kap-instant-{end_date.isoformat()}",
        end_date=end_date,
        kind="instant",
        fiscal_period="instant",
    )


def _concept(row: Tag) -> str | None:
    name_cell = row.find("td", class_="taxonomy-field-name-cell")
    if name_cell is None:
        return None
    label = name_cell.find("div", class_="taxonomy-field-name")
    if label is None:
        return None
    text = label.get_text().strip()
    concept = text.split("|", 1)[0].strip()
    if not concept:
        return None
    prefix, sep, local = concept.partition("_")
    return f"{prefix}:{local}" if sep else concept


def _decimal(cell: Tag) -> Decimal | None:
    label = cell.find("div", title=True)
    if label is None:
        return None
    raw = label.get("title", "").strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _date(match: re.Match[str]) -> date | None:
    day, month, year = match.groups()
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None
