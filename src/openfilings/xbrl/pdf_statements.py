"""Derive normalized financial statements from bounded Markdown PDF tables."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from openfilings.exceptions import ExtractionError, FinancialsUnavailableError
from openfilings.extraction.pdf import pdf_to_markdown
from openfilings.models import (
    Filing,
    FilingFinancials,
    FinancialLineItem,
    FinancialStatement,
    FinancialValue,
    ReportingPeriod,
    StatementType,
)
from openfilings.xbrl.mappings import LINE_ITEMS, LineItemDefinition

_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
_DIVIDER_CELL_PATTERN = re.compile(r"^:?-{2,}:?$")
_FOOTNOTE_PATTERN = re.compile(r"(?:\s|\*)+(?:note\s*)?\d+[a-z]?$", re.IGNORECASE)
_NON_LABEL_PATTERN = re.compile(r"[^\w\s]+", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_STATEMENT_TITLES: dict[StatementType, str] = {
    "income_statement": "Income statement",
    "balance_sheet": "Balance sheet",
    "cash_flow_statement": "Cash flow statement",
    "comprehensive_income": "Statement of comprehensive income",
    "changes_in_equity": "Statement of changes in equity",
}
_DEFINITIONS = {definition.code: definition for definition in LINE_ITEMS}
_LINE_ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": (
        "revenue",
        "total revenue",
        "operating revenue",
        "net sales",
        "sales revenue",
        "turnover",
        "receita liquida",
        "receita operacional liquida",
        "receitas",
        "ingresos",
        "ingresos de actividades ordinarias",
        "營業收入",
        "营业收入",
        "收入合計",
        "收益",
    ),
    "cost_of_revenue": (
        "cost of revenue",
        "cost of revenues",
        "cost of sales",
        "custo das vendas",
        "costo de ventas",
        "營業成本",
        "营业成本",
    ),
    "gross_profit": (
        "gross profit",
        "lucro bruto",
        "ganancia bruta",
        "utilidad bruta",
        "ganancia perdida bruta",
        "營業毛利",
        "营业毛利",
        "營業毛損",
    ),
    "operating_income_loss": (
        "operating profit",
        "operating loss",
        "profit from operations",
        "lucro operacional",
        "prejuizo operacional",
        "ganancia operativa",
        "utilidad operativa",
        "ganancia perdida operativa",
        "營業利益",
        "营业利润",
        "營業損失",
    ),
    "profit_before_tax": (
        "profit before tax",
        "loss before tax",
        "profit before income tax",
        "income before income taxes",
        "lucro antes do imposto de renda",
        "prejuizo antes do imposto de renda",
        "ganancia antes de impuestos",
        "utilidad antes de impuestos",
        "ganancia perdida antes de impuestos",
        "稅前淨利",
        "税前利润",
        "稅前淨損",
        "稅前利益",
    ),
    "income_tax": (
        "income tax expense",
        "income tax benefit",
        "imposto de renda e contribuicao social",
        "gasto por impuesto a las ganancias",
        "ingreso gasto por impuesto",
        "所得稅費用",
        "所得税费用",
        "所得稅利益",
    ),
    "net_income_loss": (
        "profit for the year",
        "loss for the year",
        "profit for the period",
        "loss for the period",
        "net profit",
        "net loss",
        "net profit after tax",
        "net loss after tax",
        "lucro liquido",
        "prejuizo liquido",
        "ganancia neta",
        "utilidad neta",
        "resultado del ejercicio",
        "ganancia perdida neta del ejercicio",
        "本期淨利",
        "净利润",
        "本期淨損",
        "本年度淨利",
        "本年度淨損",
    ),
    "basic_eps": (
        "basic earnings per share",
        "basic loss per share",
        "lucro basico por acao",
        "基本每股盈餘",
        "基本每股虧損",
    ),
    "diluted_eps": (
        "diluted earnings per share",
        "diluted loss per share",
        "lucro diluido por acao",
        "稀釋每股盈餘",
        "稀釋每股虧損",
    ),
    "total_comprehensive_income": (
        "total comprehensive income",
        "total comprehensive loss",
        "resultado abrangente total",
        "total resultado integral del ejercicio",
        "本期綜合損益總額",
    ),
    "cash_and_cash_equivalents": (
        "cash and cash equivalents",
        "caixa e equivalentes de caixa",
        "efectivo y equivalentes al efectivo",
        "現金及約當現金",
        "货币资金",
    ),
    "trade_receivables": (
        "trade and other receivables",
        "trade receivables",
        "contas a receber",
        "cuentas por cobrar comerciales",
        "應收帳款",
        "应收账款",
    ),
    "inventory": (
        "inventories",
        "inventory",
        "estoques",
        "inventarios",
        "存貨",
        "存货",
    ),
    "property_plant_equipment": (
        "property plant and equipment",
        "imobilizado",
        "propiedades planta y equipo",
        "不動產廠房及設備",
        "固定资产",
    ),
    "goodwill": ("goodwill", "agio", "商譽"),
    "intangible_assets": (
        "intangible assets",
        "intangivel",
        "activos intangibles",
        "無形資產",
    ),
    "current_assets": (
        "current assets",
        "ativo circulante",
        "activos corrientes",
        "total activos corrientes",
        "流動資產",
        "流动资产",
    ),
    "noncurrent_assets": (
        "non current assets",
        "noncurrent assets",
        "ativo nao circulante",
        "activos no corrientes",
        "total activos no corrientes",
        "非流動資產",
        "非流动资产",
    ),
    "total_assets": (
        "total assets",
        "ativo total",
        "total de activos",
        "total activos",
        "資產總計",
        "资产总计",
    ),
    "current_liabilities": (
        "current liabilities",
        "passivo circulante",
        "pasivos corrientes",
        "total pasivos corrientes",
        "流動負債",
        "流动负债",
    ),
    "noncurrent_liabilities": (
        "non current liabilities",
        "noncurrent liabilities",
        "passivo nao circulante",
        "pasivos no corrientes",
        "total pasivos no corrientes",
        "非流動負債",
        "非流动负债",
    ),
    "total_liabilities": (
        "total liabilities",
        "passivo total",
        "passivo circulante e nao circulante",
        "total de pasivos",
        "total pasivos",
        "負債總計",
        "负债合计",
        "負債總額",
    ),
    "total_equity": (
        "total equity",
        "shareholders equity",
        "patrimonio liquido",
        "total patrimonio",
        "patrimonio atribuible a los propietarios",
        "權益總計",
        "所有者权益合计",
        "權益總額",
    ),
    "operating_cash_flow": (
        "net cash from operating activities",
        "net cash used in operating activities",
        "net cash generated from operating activities",
        "cash generated from operating activities",
        "caixa liquido gerado pelas atividades operacionais",
        "flujos de efectivo y equivalente al efectivo procedente de utilizados "
        "en actividades de operacion",
        "營業活動之淨現金流入",
        "營業活動之淨現金流出",
        "營業活動淨現金流量",
    ),
    "investing_cash_flow": (
        "net cash from investing activities",
        "net cash used in investing activities",
        "net cash generated from investing activities",
        "caixa liquido aplicado nas atividades de investimento",
        "flujos de efectivo y equivalente al efectivo procedente de utilizados "
        "en actividades de inversion",
        "投資活動之淨現金流入",
        "投資活動之淨現金流出",
        "投資活動淨現金流量",
    ),
    "financing_cash_flow": (
        "net cash from financing activities",
        "net cash used in financing activities",
        "net cash generated from financing activities",
        "caixa liquido gerado pelas atividades de financiamento",
        "flujos de efectivo y equivalente al efectivo procedente de utilizados "
        "en actividades de financiacion",
        "融資活動之淨現金流入",
        "融資活動之淨現金流出",
        "籌資活動淨現金流量",
    ),
    "capital_expenditure": (
        "capital expenditure",
        "purchase of property plant and equipment",
        "aquisicao de imobilizado",
        "compra de propiedades planta y equipo",
        "取得不動產廠房及設備",
    ),
    "dividends_paid": (
        "dividends paid",
        "dividendos pagos",
        "支付股利",
        "發放現金股利",
    ),
    "net_change_in_cash": (
        "net change in cash and cash equivalents",
        "aumento liquido de caixa e equivalentes de caixa",
        "現金及約當現金淨增加",
        "現金及約當現金淨減少",
    ),
}
_CURRENCY_MARKERS = (
    ("SGD", ("s$", "sgd", "singapore dollar")),
    ("HKD", ("hk$", "hkd", "hong kong dollar")),
    ("BRL", ("r$", "brl", "reais", "real brasileiro")),
    ("TWD", ("nt$", "ntd", "twd", "新台幣")),
    ("CNY", ("rmb", "cny", "人民幣")),
    ("INR", ("inr", "₹", "indian rupee")),
    ("MXN", ("mxn", "mexican peso", "pesos mexicanos")),
    ("CAD", ("c$", "cad", "canadian dollar")),
    ("PEN", ("s/", "pen", "soles")),
    ("COP", ("cop", "pesos colombianos")),
    ("JPY", ("jpy", "円", "日圓")),
    ("USD", ("us$", "usd", "u.s. dollar")),
    ("EUR", ("eur", "€", "euro")),
    ("GBP", ("gbp", "£", "pound sterling")),
)
_DEFAULT_CURRENCY_BY_SOURCE = {
    "cvm": "BRL",
    "hkex": "HKD",
    "sgx": "SGD",
    "twse": "TWD",
    "bmv": "MXN",
    "nse": "INR",
    "sedar": "CAD",
    "cninfo": "CNY",
    "smv": "PEN",
    "sfc": "COP",
}
_STATEMENT_HEADINGS: dict[StatementType, tuple[str, ...]] = {
    "income_statement": (
        "statement of comprehensive income",
        "consolidated income statement",
        "income statement",
        "statement of profit or loss",
        "demonstracao do resultado",
        "estado de resultados",
        "estado del resultado",
        "綜合損益表",
        "利润表",
        "損益表",
    ),
    "balance_sheet": (
        "statement of financial position",
        "consolidated balance sheet",
        "balance sheet",
        "balanco patrimonial",
        "estado de situacion financiera",
        "estado de situación financiera",
        "資產負債表",
        "资产负债表",
    ),
    "cash_flow_statement": (
        "consolidated statement of cash flows",
        "statement of cash flows",
        "cash flow statement",
        "demonstracao dos fluxos de caixa",
        "estado de flujos de efectivo",
        "現金流量表",
        "现金流量表",
    ),
    "comprehensive_income": (
        "statement of other comprehensive income",
        "demonstracao do resultado abrangente",
        "estado de resultados integrales",
        "其他綜合損益",
    ),
    "changes_in_equity": (
        "statement of changes in equity",
        "demonstracao das mutacoes do patrimonio liquido",
        "estado de cambios en el patrimonio",
        "權益變動表",
        "所有者权益变动表",
    ),
}


@dataclass(frozen=True, slots=True)
class _MarkdownTable:
    context: str
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class _TableFormat:
    periods: tuple[tuple[int, int], ...]
    currency: str | None
    scale: Decimal
    decimals: str


def extract_pdf_table_financials(
    markdown: str,
    filing: Filing,
    *,
    source_url: str,
    sha256: str,
    extraction_method: str = "pdf-markdown-tables",
) -> FilingFinancials:
    """Return high-confidence standardized values from Markdown table rows."""

    selected: dict[str, FinancialLineItem] = {}
    for table in _markdown_tables(markdown):
        line_items = _line_items_from_table(table, filing)
        if len(line_items) < 2:
            continue
        for item in line_items:
            previous = selected.get(item.code)
            if previous is None or len(item.values) > len(previous.values):
                selected[item.code] = item

    statements = _statements(tuple(selected.values()))
    if not statements:
        raise FinancialsUnavailableError(
            "The PDF contains no high-confidence supported statement tables."
        )
    return FilingFinancials(
        filing_id=filing.id,
        company_id=filing.company_id,
        source_url=source_url,
        extraction_method=extraction_method,
        statements=statements,
        fact_count=sum(
            len(item.values)
            for statement in statements
            for item in statement.line_items
        ),
        taxonomy_namespaces=("pdf-derived",),
        sha256=sha256,
    )


def extract_pdf_text_financials(
    sections: tuple[str, ...],
    filing: Filing,
    *,
    source_url: str,
    sha256: str,
    extraction_method: str = "pdf-aligned-text",
) -> FilingFinancials:
    """Return standardized values from aligned text on statement pages."""

    selected: dict[str, FinancialLineItem] = {}
    for section in sections:
        section_items = _line_items_from_text(section, filing)
        if len(section_items) < 2:
            continue
        for item in section_items:
            previous = selected.get(item.code)
            if previous is None or len(item.values) > len(previous.values):
                selected[item.code] = item
    statements = _statements(tuple(selected.values()))
    if not statements:
        raise FinancialsUnavailableError(
            "The PDF contains no high-confidence aligned statement text."
        )
    return FilingFinancials(
        filing_id=filing.id,
        company_id=filing.company_id,
        source_url=source_url,
        extraction_method=extraction_method,
        statements=statements,
        fact_count=sum(
            len(item.values)
            for statement in statements
            for item in statement.line_items
        ),
        taxonomy_namespaces=("pdf-derived",),
        sha256=sha256,
    )


def extract_pdf_ocr_financials(
    markdown: str,
    filing: Filing,
    *,
    source_url: str,
    sha256: str,
) -> FilingFinancials:
    """Return structured values from page-delimited OCR output."""

    pages = tuple(
        page.strip()
        for page in re.split(r"(?m)^## Page \d+\s*$", markdown)
        if page.strip()
    )
    if not pages:
        pages = (markdown,)
    sections = tuple(
        f"{page}\n{pages[index + 1] if index + 1 < len(pages) else ''}"
        for index, page in enumerate(pages)
        if _statement_type(
            tuple(line.strip() for line in page.splitlines() if line.strip())
        )
        is not None
    )
    return extract_pdf_text_financials(
        sections,
        filing,
        source_url=source_url,
        sha256=sha256,
        extraction_method="pdf-ocr-text",
    )


def extract_pdf_source_financials(
    pdf_bytes: bytes,
    filing: Filing,
    *,
    source_url: str,
    sha256: str,
) -> FilingFinancials:
    """Extract a PDF through aligned text, then Markdown tables as fallback."""

    sections = _pdf_statement_sections(pdf_bytes)
    if sections:
        try:
            return extract_pdf_text_financials(
                sections,
                filing,
                source_url=source_url,
                sha256=sha256,
            )
        except FinancialsUnavailableError:
            pass
    try:
        markdown = pdf_to_markdown(pdf_bytes)
    except ExtractionError as exc:
        raise FinancialsUnavailableError(
            f"The filing PDF could not be read for financial tables: {exc}"
        ) from exc
    return extract_pdf_table_financials(
        markdown,
        filing,
        source_url=source_url,
        sha256=sha256,
    )


def _markdown_tables(markdown: str) -> tuple[_MarkdownTable, ...]:
    lines = [line.strip() for line in markdown.splitlines()]
    tables: list[_MarkdownTable] = []
    index = 0
    while index < len(lines):
        if not _is_table_line(lines[index]):
            index += 1
            continue
        start = index
        raw_rows: list[str] = []
        while index < len(lines) and _is_table_line(lines[index]):
            raw_rows.append(lines[index])
            index += 1
        rows = tuple(_split_row(row) for row in raw_rows if not _is_divider_row(row))
        if len(rows) >= 2:
            context = "\n".join(lines[max(0, start - 8) : start])
            tables.append(_MarkdownTable(context=context, rows=rows))
    return tuple(tables)


def _line_items_from_text(
    section: str, filing: Filing
) -> tuple[FinancialLineItem, ...]:
    lines = tuple(line.strip() for line in section.splitlines() if line.strip())
    statement_type = _statement_type(lines)
    years = _text_years(lines)
    if statement_type is None or not years:
        return ()
    header = "\n".join(lines[:30])
    currency = _currency(header) or _DEFAULT_CURRENCY_BY_SOURCE.get(filing.source)
    scale = _scale(header)
    decimals = f"-{len(str(int(scale))) - 1}" if scale > 1 else "0"
    items: dict[str, FinancialLineItem] = {}
    for index, label in enumerate(lines):
        definition = _definition_for_label(label)
        if definition is None or definition.statement_type != statement_type:
            continue
        numbers = _aligned_numbers(lines, index + 1, len(years), definition)
        if not numbers:
            continue
        values = _values_from_numbers(
            numbers,
            years,
            definition,
            filing,
            currency=currency,
            scale=scale,
            decimals=decimals,
        )
        items[definition.code] = FinancialLineItem(
            code=definition.code,
            name=definition.name,
            concept=f"pdf-label:{_concept_label(label)}",
            values=values,
        )
    return tuple(items.values())


def _pdf_statement_sections(pdf_bytes: bytes) -> tuple[str, ...]:
    try:
        import pymupdf

        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            page_texts = tuple(page.get_text() for page in document)
        finally:
            document.close()
    except Exception as exc:
        raise FinancialsUnavailableError(
            f"The filing PDF could not be inspected for statement pages: {exc}"
        ) from exc

    if sum(len(text.strip()) for text in page_texts) < 100:
        raise FinancialsUnavailableError(
            "The filing PDF is image-only; structured financial tables require "
            "OCR. Automatic OCR is used by OpenFilingsService when enabled and "
            "Tesseract is installed."
        )

    sections: list[str] = []
    for index, text in enumerate(page_texts):
        lines = tuple(line.strip() for line in text.splitlines() if line.strip())
        if _statement_type(lines) is None:
            continue
        continuation = page_texts[index + 1] if index + 1 < len(page_texts) else ""
        sections.append(f"{text}\n{continuation}")
    return tuple(sections)


def _line_items_from_table(
    table: _MarkdownTable, filing: Filing
) -> tuple[FinancialLineItem, ...]:
    table_format = _table_format(table)
    if table_format is None:
        return ()
    if table_format.currency is None:
        table_format = _TableFormat(
            periods=table_format.periods,
            currency=_DEFAULT_CURRENCY_BY_SOURCE.get(filing.source),
            scale=table_format.scale,
            decimals=table_format.decimals,
        )
    items: dict[str, FinancialLineItem] = {}
    first_value_column = min(column for column, _ in table_format.periods)
    for row in table.rows:
        if len(row) <= first_value_column:
            continue
        label = _row_label(row[:first_value_column])
        definition = _definition_for_label(label)
        if definition is None:
            continue
        values = _row_values(row, definition, table_format, filing)
        if not values:
            continue
        items[definition.code] = FinancialLineItem(
            code=definition.code,
            name=definition.name,
            concept=f"pdf-label:{_concept_label(label)}",
            values=values,
        )
    return tuple(items.values())


def _table_format(table: _MarkdownTable) -> _TableFormat | None:
    header = max(table.rows[:4], key=_year_cell_count)
    period_columns: list[tuple[int, int]] = []
    seen_years: set[int] = set()
    for column, cell in enumerate(header):
        match = _YEAR_PATTERN.search(cell)
        if match is None:
            continue
        year = int(match.group(1))
        if year not in seen_years:
            seen_years.add(year)
            period_columns.append((column, year))
    if not period_columns:
        return None
    context = f"{table.context}\n{' '.join(header)}"
    scale = _scale(context)
    return _TableFormat(
        periods=tuple(period_columns),
        currency=_currency(context),
        scale=scale,
        decimals=f"-{len(str(int(scale))) - 1}" if scale > 1 else "0",
    )


def _row_values(
    row: tuple[str, ...],
    definition: LineItemDefinition,
    table_format: _TableFormat,
    filing: Filing,
) -> tuple[FinancialValue, ...]:
    values: list[FinancialValue] = []
    scale = (
        Decimal(1)
        if definition.code in {"basic_eps", "diluted_eps"}
        else table_format.scale
    )
    for column, year in table_format.periods:
        if column >= len(row):
            continue
        number = _number(row[column])
        if number is None:
            continue
        period = _reporting_period(year, definition.statement_type, filing)
        unit = table_format.currency
        if definition.code in {"basic_eps", "diluted_eps"} and unit:
            unit = f"{unit} / shares"
        values.append(
            FinancialValue(
                period=period,
                value=number * scale,
                unit=unit,
                decimals=(
                    "0"
                    if definition.code in {"basic_eps", "diluted_eps"}
                    else table_format.decimals
                ),
            )
        )
    return tuple(values)


def _values_from_numbers(
    numbers: tuple[Decimal, ...],
    years: tuple[int, ...],
    definition: LineItemDefinition,
    filing: Filing,
    *,
    currency: str | None,
    scale: Decimal,
    decimals: str,
) -> tuple[FinancialValue, ...]:
    item_scale = (
        Decimal(1) if definition.code in {"basic_eps", "diluted_eps"} else scale
    )
    unit = currency
    if definition.code in {"basic_eps", "diluted_eps"} and unit:
        unit = f"{unit} / shares"
    return tuple(
        FinancialValue(
            period=_reporting_period(year, definition.statement_type, filing),
            value=number * item_scale,
            unit=unit,
            decimals=(
                "0" if definition.code in {"basic_eps", "diluted_eps"} else decimals
            ),
        )
        for number, year in zip(numbers, years, strict=True)
    )


def _aligned_numbers(
    lines: tuple[str, ...],
    start: int,
    period_count: int,
    definition: LineItemDefinition,
) -> tuple[Decimal, ...]:
    window = lines[start : start + 14]
    # Normally a row's own values sit directly on the lines right after its
    # label (a footnote digit may lead them), so the first period_count
    # numbers found are correct and scanning should not chase later,
    # unrelated rows. But when the label is immediately followed by
    # unlabeled sub-item text (e.g. "Revenues" broken down by segment with
    # no repeated "Total revenue" line), the row's own total instead sits
    # in the run of numbers closest to the next recognized line item. A
    # breakdown can span more lines than a plain row, so it gets a larger
    # scan window to still reach its own total.
    first_line = window[0] if window else None
    leads_with_text = (
        first_line is not None
        and _number(first_line) is None
        and _definition_for_label(first_line) is None
    )
    if leads_with_text:
        return _breakdown_aligned_numbers(
            lines[start : start + 40], period_count, definition
        )
    numbers: list[Decimal] = []
    for line in window:
        next_definition = _definition_for_label(line)
        if next_definition is not None and next_definition.code != definition.code:
            break
        number = _number(line)
        if number is not None:
            numbers.append(number)
    if len(numbers) > period_count and _looks_like_note(numbers[0], numbers[1:]):
        numbers.pop(0)
    if len(numbers) < period_count:
        return ()
    return tuple(numbers[:period_count])


def _breakdown_aligned_numbers(
    window: tuple[str, ...],
    period_count: int,
    definition: LineItemDefinition,
) -> tuple[Decimal, ...]:
    """Resolve a label followed by an unlabeled breakdown before its total.

    Numbers are grouped into runs split by non-numeric lines. The row's own
    total is the run closest to the next recognized line item, not the
    first run found.
    """
    runs: list[list[Decimal]] = [[]]
    for line in window:
        next_definition = _definition_for_label(line)
        if next_definition is not None and next_definition.code != definition.code:
            break
        number = _number(line)
        if number is None:
            if runs[-1]:
                runs.append([])
            continue
        runs[-1].append(number)
    runs = [run for run in runs if run]
    if not runs:
        return ()
    candidate = runs[-1]
    if len(candidate) > period_count and _looks_like_note(
        candidate[0], candidate[1:]
    ):
        candidate = candidate[1:]
    if len(candidate) > period_count:
        split = _footnote_split(candidate, period_count)
        if split is not None:
            candidate = candidate[split:]
    if len(candidate) < period_count:
        return ()
    return tuple(candidate[:period_count])


def _footnote_split(candidate: list[Decimal], period_count: int) -> int | None:
    """Find a footnote marker directly ahead of the row's own values.

    A footnote reference can sit right before a row's total (possibly after
    an unlabeled breakdown sharing the same run, with nothing textual to
    separate them). If the marker is positioned so exactly `period_count`
    values follow it, that marker is the boundary.
    """
    index = len(candidate) - period_count - 1
    if index < 0:
        return None
    if _looks_like_note(candidate[index], candidate[index + 1 :]):
        return index + 1
    return None


def _looks_like_note(value: Decimal, following: list[Decimal]) -> bool:
    return (
        value == value.to_integral_value()
        and 0 <= value <= 99
        and any(abs(candidate) >= 100 for candidate in following)
    )


def _statement_type(lines: tuple[str, ...]) -> StatementType | None:
    opening = tuple(_normalize_label(line) for line in lines[:12])
    for statement_type, headings in _STATEMENT_HEADINGS.items():
        if any(
            normalized_heading in line
            for line in opening
            for heading in headings
            for normalized_heading in (_normalize_label(heading),)
        ):
            return statement_type
    return None


def _text_years(lines: tuple[str, ...]) -> tuple[int, ...]:
    years: list[int] = []
    for line in lines[:30]:
        for match in _YEAR_PATTERN.finditer(line):
            year = int(match.group(1))
            if year not in years:
                years.append(year)
    return tuple(years[:2])


def _statements(
    items: tuple[FinancialLineItem, ...],
) -> tuple[FinancialStatement, ...]:
    statements: list[FinancialStatement] = []
    for statement_type, title in _STATEMENT_TITLES.items():
        statement_items = tuple(
            item
            for item in items
            if _DEFINITIONS[item.code].statement_type == statement_type
        )
        if not statement_items:
            continue
        currencies = {
            value.unit.split(" / ", 1)[0]
            for item in statement_items
            for value in item.values
            if value.unit
        }
        statements.append(
            FinancialStatement(
                statement_type=statement_type,
                title=title,
                currency=next(iter(currencies)) if len(currencies) == 1 else None,
                line_items=statement_items,
            )
        )
    return tuple(statements)


def _definition_for_label(label: str) -> LineItemDefinition | None:
    normalized = _normalize_label(label)
    # An exact match is checked across every code first. A subtotal label
    # can otherwise start with a different line item's full name plus a
    # conjunction (e.g. "Passivo circulante e nao circulante" - total
    # liabilities - starts with "Passivo circulante" - current liabilities),
    # which the looser prefix match below would misclassify.
    for code, aliases in _LINE_ITEM_ALIASES.items():
        for alias in aliases:
            if normalized == _normalize_label(alias):
                return _DEFINITIONS[code]
    for code, aliases in _LINE_ITEM_ALIASES.items():
        for alias in aliases:
            normalized_alias = _normalize_label(alias)
            if normalized.startswith(f"{normalized_alias} "):
                return _DEFINITIONS[code]
            if " " not in normalized_alias and normalized.startswith(normalized_alias):
                return _DEFINITIONS[code]
    return None


def _reporting_period(
    year: int, statement_type: StatementType, filing: Filing
) -> ReportingPeriod:
    end_date = _period_end(year, filing.period_end)
    if statement_type == "balance_sheet":
        return ReportingPeriod(
            id=f"pdf-instant-{end_date.isoformat()}",
            end_date=end_date,
            kind="instant",
            fiscal_period="instant",
        )
    start_date = _duration_start(end_date, filing)
    fiscal_period = _fiscal_period(start_date, end_date)
    return ReportingPeriod(
        id=f"pdf-{fiscal_period.casefold()}-{end_date.isoformat()}",
        start_date=start_date,
        end_date=end_date,
        kind="duration",
        fiscal_period=fiscal_period,
    )


def _period_end(year: int, filing_period_end: date | None) -> date:
    if filing_period_end is None:
        return date(year, 12, 31)
    return _replace_year(filing_period_end, year)


def _replace_year(value: date, year: int) -> date:
    try:
        return value.replace(year=year)
    except ValueError:
        return value.replace(year=year, day=28)


def _duration_start(end_date: date, filing: Filing) -> date:
    if filing.period_start is not None:
        return _replace_year(filing.period_start, end_date.year)
    if filing.filing_type.casefold() == "annual":
        previous_end = _replace_year(end_date, end_date.year - 1)
        return previous_end + timedelta(days=1)
    return date(end_date.year, 1, 1)


def _fiscal_period(start_date: date, end_date: date) -> str:
    days = (end_date - start_date).days + 1
    if 75 <= days <= 105:
        return "Q"
    if 170 <= days <= 200:
        return "H1"
    if 250 <= days <= 290:
        return "9M"
    if 300 <= days <= 380:
        return "FY"
    return "YTD"


def _row_label(cells: tuple[str, ...]) -> str:
    for cell in cells:
        clean = _FOOTNOTE_PATTERN.sub("", cell).strip()
        if clean and not clean.isdigit() and clean.casefold() not in {"note", "notes"}:
            return clean
    return ""


def _number(value: str) -> Decimal | None:
    clean = value.strip().replace("\u00a0", " ")
    if not clean or clean.casefold() in {
        "-",
        "\N{EM DASH}",
        "\N{EN DASH}",
        "n/a",
        "nm",
    }:
        return None
    negative = clean.startswith("(") and clean.endswith(")")
    clean = clean.strip("() ").replace(" ", "").replace("'", "")
    clean = re.sub(r"[^0-9,.-]", "", clean)
    if clean.count("-") > 1 or ("-" in clean and not clean.startswith("-")):
        return None
    clean = _normalized_number(clean)
    try:
        number = Decimal(clean)
    except InvalidOperation:
        return None
    return -abs(number) if negative else number


def _normalized_number(value: str) -> str:
    unsigned = value.lstrip("-")
    sign = "-" if value.startswith("-") else ""
    if "," in unsigned and "." in unsigned:
        decimal = "," if unsigned.rfind(",") > unsigned.rfind(".") else "."
        grouping = "." if decimal == "," else ","
        return sign + unsigned.replace(grouping, "").replace(decimal, ".")
    if "," in unsigned:
        return sign + _single_separator_number(unsigned, ",")
    if "." in unsigned:
        return sign + _single_separator_number(unsigned, ".")
    return value


def _single_separator_number(value: str, separator: str) -> str:
    groups = value.split(separator)
    if len(groups) > 1 and all(len(group) == 3 for group in groups[1:]):
        return "".join(groups)
    return value.replace(separator, ".")


def _currency(value: str) -> str | None:
    normalized = value.casefold()
    return next(
        (
            code
            for code, markers in _CURRENCY_MARKERS
            if any(_has_currency_marker(normalized, marker) for marker in markers)
        ),
        None,
    )


def _has_currency_marker(value: str, marker: str) -> bool:
    normalized_marker = marker.casefold()
    if normalized_marker.isascii() and normalized_marker.replace(" ", "").isalnum():
        return bool(
            re.search(
                rf"(?<!\w){re.escape(normalized_marker)}(?!\w)",
                value,
            )
        )
    return normalized_marker in value


def _scale(value: str) -> Decimal:
    normalized = _normalize_label(value)
    if any(marker in normalized for marker in ("billion", "bilhoes", "十億元")):
        return Decimal(1_000_000_000)
    if any(marker in normalized for marker in ("million", "milhoes", "百萬元", "百萬")):
        return Decimal(1_000_000)
    if any(
        marker in normalized
        for marker in ("thousand", "milhares", "000", "千元", "仟元")
    ):
        return Decimal(1_000)
    return Decimal(1)


def _normalize_label(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return _WHITESPACE_PATTERN.sub(
        " ", _NON_LABEL_PATTERN.sub(" ", without_marks)
    ).strip()


def _concept_label(value: str) -> str:
    return _normalize_label(value).replace(" ", "-") or "unknown"


def _is_table_line(value: str) -> bool:
    return value.startswith("|") and value.endswith("|") and value.count("|") >= 3


def _split_row(value: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in value.strip("|").split("|"))


def _is_divider_row(value: str) -> bool:
    cells = _split_row(value)
    return bool(cells) and all(_DIVIDER_CELL_PATTERN.fullmatch(cell) for cell in cells)


def _year_cell_count(row: tuple[str, ...]) -> int:
    return sum(_YEAR_PATTERN.search(cell) is not None for cell in row)
