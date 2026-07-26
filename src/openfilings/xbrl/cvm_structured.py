"""Structured financial statements from CVM's Open Data DFP/ITR datasets.

CVM mandates a standardized chart of accounts (Plano de Contas) for every
DFP (annual) and ITR (quarterly) filing, published as bulk CSV/ZIP files at
dados.cvm.gov.br. Each row is already isolated to one account and one value,
so this path only needs label-to-concept mapping - no column-alignment
guessing the way PDF-derived text needs.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation

from openfilings.exceptions import FinancialsUnavailableError, SourceError
from openfilings.models import (
    Filing,
    FilingFinancials,
    FinancialLineItem,
    FinancialStatement,
    FinancialValue,
    ReportingPeriod,
    StatementType,
)
from openfilings.xbrl.mappings import LINE_ITEMS

_DEFINITIONS = {definition.code: definition for definition in LINE_ITEMS}
_STATEMENT_TITLES: dict[StatementType, str] = {
    "income_statement": "Income statement",
    "balance_sheet": "Balance sheet",
    "cash_flow_statement": "Cash flow statement",
}
_MAX_MEMBER_BYTES = 100 * 1024 * 1024

# Statement groups (GRUPO_DFP) that carry the primary consolidated figures.
# CVM also publishes standalone/parent-only ("_ind_") files; consolidated
# ("_con_") is preferred to match how OpenFilings reports every other market.
_STATEMENT_MEMBERS: dict[StatementType, str] = {
    "balance_sheet": "BPA_con",  # assets; liabilities/equity follow below
    "income_statement": "DRE_con",
    "cash_flow_statement": "DFC_MI_con",
}
_BALANCE_SHEET_LIABILITIES_MEMBER = "BPP_con"

# CVM's chart of accounts is standardized, but the same numeric code can
# mean a different thing between industry-specific DFP layouts (banks and
# insurers report a different DRE shape than ordinary companies), so labels
# are matched by their normalized text, not by CD_CONTA position.
_ACCOUNT_ALIASES: dict[str, tuple[str, ...]] = {
    "cash_and_cash_equivalents": ("caixa e equivalentes de caixa",),
    "trade_receivables": ("contas a receber",),
    "inventory": ("estoques",),
    "property_plant_equipment": ("imobilizado",),
    "intangible_assets": ("intangivel",),
    "current_assets": ("ativo circulante",),
    "noncurrent_assets": ("ativo nao circulante",),
    "total_assets": ("ativo total",),
    "current_liabilities": ("passivo circulante",),
    "noncurrent_liabilities": ("passivo nao circulante",),
    "total_equity": (
        "patrimonio liquido consolidado",
        "patrimonio liquido",
    ),
    "revenue": (
        "receita de venda de bens e/ou servicos",
        "receitas de intermediacao financeira",
        "receita liquida",
    ),
    "cost_of_revenue": (
        "custo dos bens e/ou servicos vendidos",
        "despesas de intermediacao financeira",
    ),
    "gross_profit": (
        "resultado bruto de intermediacao financeira",
        "resultado bruto",
    ),
    "operating_income_loss": (
        "resultado antes do resultado financeiro e dos tributos",
    ),
    "profit_before_tax": (
        "resultado antes dos tributos sobre o lucro",
        "resultado antes dos tributos sobre o lucro e participacoes",
    ),
    "income_tax": ("imposto de renda e contribuicao social sobre o lucro",),
    "net_income_loss": (
        "lucro/prejuizo consolidado do periodo",
        "lucro ou prejuizo liquido consolidado do periodo",
        "lucro ou prejuizo consolidado do periodo",
    ),
    "basic_eps": ("lucro por acao - (reais / acao)", "lucro por acao (r$/acao)"),
    "operating_cash_flow": ("caixa liquido atividades operacionais",),
    "investing_cash_flow": ("caixa liquido atividades de investimento",),
    "financing_cash_flow": ("caixa liquido atividades de financiamento",),
    "net_change_in_cash": ("aumento (reducao) de caixa e equivalentes",),
}


def _normalize_label(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s/()]+", " ", without_marks)).strip()


_CODE_BY_LABEL = {
    _normalize_label(alias): code
    for code, aliases in _ACCOUNT_ALIASES.items()
    for alias in aliases
}


def extract_cvm_structured_financials(
    archive_bytes: bytes,
    filing: Filing,
    *,
    cd_cvm: str,
    source_url: str,
    sha256: str,
) -> FilingFinancials:
    """Build normalized statements from a CVM DFP/ITR bulk dataset ZIP."""

    statements: list[FinancialStatement] = []
    fact_count = 0

    balance_sheet_items = _statement_line_items(
        archive_bytes, "BPA_con", cd_cvm, filing, "balance_sheet"
    ) + _statement_line_items(
        archive_bytes,
        _BALANCE_SHEET_LIABILITIES_MEMBER,
        cd_cvm,
        filing,
        "balance_sheet",
    )
    balance_sheet_items = _with_derived_total_liabilities(balance_sheet_items)
    if balance_sheet_items:
        currency = _statement_currency(balance_sheet_items)
        statements.append(
            FinancialStatement(
                statement_type="balance_sheet",
                title=_STATEMENT_TITLES["balance_sheet"],
                currency=currency,
                line_items=balance_sheet_items,
            )
        )
        fact_count += sum(len(item.values) for item in balance_sheet_items)

    for statement_type, member_suffix in (
        ("income_statement", "DRE_con"),
        ("cash_flow_statement", "DFC_MI_con"),
    ):
        items = _statement_line_items(
            archive_bytes, member_suffix, cd_cvm, filing, statement_type
        )
        if not items:
            continue
        statements.append(
            FinancialStatement(
                statement_type=statement_type,
                title=_STATEMENT_TITLES[statement_type],
                currency=_statement_currency(items),
                line_items=items,
            )
        )
        fact_count += sum(len(item.values) for item in items)

    if not statements:
        raise FinancialsUnavailableError(
            "CVM's structured dataset has no rows for this company and period."
        )

    return FilingFinancials(
        filing_id=filing.id,
        company_id=filing.company_id,
        source_url=source_url,
        extraction_method="cvm-open-data",
        statements=tuple(statements),
        fact_count=fact_count,
        taxonomy_namespaces=("cvm-plano-de-contas",),
        sha256=sha256,
    )


def _with_derived_total_liabilities(
    items: tuple[FinancialLineItem, ...],
) -> tuple[FinancialLineItem, ...]:
    """CVM's chart of accounts has no single "Passivo Total" line - only
    Passivo Circulante and Passivo Não Circulante, which combine directly
    with equity for the balance-sheet total. Derive total_liabilities from
    those two so it reconciles the same way every other market's does."""

    if any(item.code == "total_liabilities" for item in items):
        return items
    current = next((item for item in items if item.code == "current_liabilities"), None)
    noncurrent = next(
        (item for item in items if item.code == "noncurrent_liabilities"), None
    )
    if current is None or noncurrent is None:
        return items
    noncurrent_by_period = {value.period.label: value for value in noncurrent.values}
    values = tuple(
        FinancialValue(
            period=current_value.period,
            value=current_value.value + matching.value,
            unit=current_value.unit,
            decimals=current_value.decimals,
            provenance="derived",
            confidence=min(current_value.confidence, matching.confidence),
            derived_from=("current_liabilities", "noncurrent_liabilities"),
        )
        for current_value in current.values
        if (matching := noncurrent_by_period.get(current_value.period.label))
        is not None
    )
    if not values:
        return items
    definition = _DEFINITIONS["total_liabilities"]
    derived = FinancialLineItem(
        code="total_liabilities",
        name=definition.name,
        concept="cvm-plano-de-contas:total_liabilities (derived)",
        values=values,
    )
    return (*items, derived)


def _statement_line_items(
    archive_bytes: bytes,
    member_suffix: str,
    cd_cvm: str,
    filing: Filing,
    statement_type: StatementType,
) -> tuple[FinancialLineItem, ...]:
    rows = _member_rows(archive_bytes, member_suffix)
    # (value, period, account-code depth) per code/period; on a label
    # collision (e.g. "Estoques" used both for current-asset inventory and
    # an unrelated nested long-term breakdown), the shallower account code
    # is the primary line item and wins.
    by_code: dict[str, dict[str, tuple[Decimal, ReportingPeriod, int]]] = {}
    for row in rows:
        if _format_cvm_code(row.get("CD_CVM", "")) != cd_cvm:
            continue
        code = _CODE_BY_LABEL.get(_normalize_label(row.get("DS_CONTA", "")))
        if code is None or _DEFINITIONS[code].statement_type != statement_type:
            continue
        value = _decimal(row.get("VL_CONTA"))
        if value is None:
            continue
        scale = _scale(row.get("ESCALA_MOEDA", ""))
        period = _period(row, definition_statement_type=statement_type)
        if period is None:
            continue
        order_key = _normalize_label(row.get("ORDEM_EXERC", ""))
        depth = row.get("CD_CONTA", "").count(".")
        existing = by_code.setdefault(code, {}).get(order_key)
        if existing is not None and existing[2] <= depth:
            continue
        by_code[code][order_key] = (value * scale, period, depth)

    items: list[FinancialLineItem] = []
    for code, by_order in by_code.items():
        definition = _DEFINITIONS[code]
        currency = _currency(rows)
        values = tuple(
            FinancialValue(
                period=period,
                value=value,
                unit=currency,
                decimals="0",
                provenance="regulated_structured_data",
                confidence=95,
            )
            for value, period, _depth in (
                by_order.get(order)
                for order in ("ultimo", "penultimo")
                if by_order.get(order) is not None
            )
        )
        if values:
            items.append(
                FinancialLineItem(
                    code=code,
                    name=definition.name,
                    concept=f"cvm-plano-de-contas:{code}",
                    values=values,
                )
            )
    return tuple(items)


def _member_rows(archive_bytes: bytes, member_suffix: str) -> list[dict[str, str]]:
    # Member filenames carry a trailing year, e.g. "..._BPA_con_2025.csv".
    marker = f"_{member_suffix}_".casefold()
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            member = next(
                (
                    info
                    for info in archive.infolist()
                    if not info.is_dir()
                    and marker in info.filename.casefold()
                    and info.filename.casefold().endswith(".csv")
                ),
                None,
            )
            if member is None:
                return []
            if member.file_size > _MAX_MEMBER_BYTES:
                raise SourceError(
                    f"The CVM {member_suffix} dataset expands beyond the safe limit."
                )
            data = archive.read(member)
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise SourceError("The CVM structured dataset archive is invalid.") from exc
    try:
        text = data.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise SourceError("CVM returned invalid CSV encoding.") from exc
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    return [dict(row) for row in reader]


def _period(
    row: dict[str, str], *, definition_statement_type: StatementType
) -> ReportingPeriod | None:
    end = _parse_date(row.get("DT_FIM_EXERC"))
    if end is None:
        return None
    if definition_statement_type == "balance_sheet":
        return ReportingPeriod(
            id=f"cvm-instant-{end.isoformat()}",
            end_date=end,
            kind="instant",
            fiscal_period="instant",
        )
    start = _parse_date(row.get("DT_INI_EXERC"))
    if start is None:
        return None
    days = (end - start).days + 1
    if 75 <= days <= 105:
        fiscal_period = "Q"
    elif 170 <= days <= 200:
        fiscal_period = "H1"
    elif 250 <= days <= 290:
        fiscal_period = "9M"
    else:
        fiscal_period = "FY"
    return ReportingPeriod(
        id=f"cvm-{fiscal_period.casefold()}-{end.isoformat()}",
        start_date=start,
        end_date=end,
        kind="duration",
        fiscal_period=fiscal_period,
    )


def _statement_currency(items: tuple[FinancialLineItem, ...]) -> str | None:
    for item in items:
        for value in item.values:
            if value.unit:
                return value.unit
    return None


def _currency(rows: list[dict[str, str]]) -> str | None:
    for row in rows:
        code = row.get("MOEDA", "").strip().upper()
        if code == "REAL":
            return "BRL"
        if code:
            return code
    return None


def _scale(value: str) -> Decimal:
    normalized = value.strip().casefold()
    if normalized in {"mil", "milhares"}:
        return Decimal(1000)
    if normalized in {"milhao", "milhoes"}:
        return Decimal(1_000_000)
    return Decimal(1)


def _decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.strip().replace(",", "."))
    except InvalidOperation:
        return None


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _format_cvm_code(value: str) -> str:
    return value.strip().zfill(6)
