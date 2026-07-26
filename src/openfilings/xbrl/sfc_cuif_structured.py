"""Structured balance-sheet data from SFC's CUIF dataset (datos.gov.co).

Colombia's SFC mandates a standardized supervisory chart of accounts (CUIF -
Catalogo Unico de Informacion Financiera) for every regulated entity type
(banks, insurers, pension fund managers, brokers, ...), published on
datos.gov.co. Balance-sheet accounts (class 1/2/3: assets, liabilities,
equity) are stock figures and reconcile exactly. Income/expense accounts
(class 4/5) are reported unclosed for supervisory purposes - revenue exactly
equals expenses even at year-end - so they are not a usable income statement
and are deliberately left out of this module; the income statement still
comes from the existing PDF filing.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from openfilings.models import (
    Filing,
    FinancialLineItem,
    FinancialStatement,
    FinancialValue,
    ReportingPeriod,
)

# CUIF's balance-sheet accounts are unified across every SFC-regulated entity
# type - verified against both a bank (Bancolombia) and a life insurer
# (Allianz Seguros de Vida), where account 100000 reads "ACTIVO" in both.
_ACCOUNT_CODES: dict[str, str] = {
    "100000": "total_assets",
    "200000": "total_liabilities",
    "300000": "total_equity",
    "110000": "cash_and_cash_equivalents",
}


def extract_sfc_cuif_balance_sheet(
    rows: list[dict[str, object]],
    filing: Filing,
) -> FinancialStatement | None:
    """Return a balance sheet built from CUIF rows, or None if no recognized
    account codes were found in this batch."""

    if filing.period_end is None:
        return None
    period = ReportingPeriod(
        id=f"sfc-cuif-instant-{filing.period_end.isoformat()}",
        end_date=filing.period_end,
        kind="instant",
        fiscal_period="instant",
    )
    items: dict[str, FinancialLineItem] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = _ACCOUNT_CODES.get(str(row.get("cuenta", "")).strip())
        if code is None or code in items:
            continue
        value = _amount(row)
        if value is None:
            continue
        items[code] = FinancialLineItem(
            code=code,
            name=code.replace("_", " ").title(),
            concept=f"sfc-cuif:{row.get('cuenta')}",
            values=(
                FinancialValue(
                    period=period,
                    value=value,
                    unit="COP",
                    decimals="0",
                    provenance="regulated_structured_data",
                    confidence=95,
                ),
            ),
        )
    if not items:
        return None
    return FinancialStatement(
        statement_type="balance_sheet",
        title="Balance sheet",
        currency="COP",
        line_items=tuple(items.values()),
    )


def _amount(row: dict[str, object]) -> Decimal | None:
    try:
        value = Decimal(str(row.get("valor", "")).strip())
    except InvalidOperation:
        return None
    return -value if str(row.get("signo_valor", "")).strip() == "-" else value
