"""Normalize tagged facts or high-confidence PDF tables into statements."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import date

from openfilings.adapters.base import SourceDocument
from openfilings.bmv_json import parse_bmv_json_xbrl
from openfilings.exceptions import FinancialsUnavailableError
from openfilings.extraction.document import (
    html_documents_from_zip,
    main_html_from_zip,
)
from openfilings.extraction.html import html_to_markdown
from openfilings.models import (
    Filing,
    FilingFinancials,
    FinancialLineItem,
    FinancialStatement,
    FinancialValue,
    ReportingPeriod,
    StatementType,
)
from openfilings.xbrl.mappings import LINE_ITEMS, definition_for_concept
from openfilings.xbrl.parser import ParsedXbrl, XbrlContext, XbrlFact, parse_inline_xbrl
from openfilings.xbrl.pdf_statements import (
    extract_pdf_source_financials,
    extract_pdf_table_financials,
)

_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_ZIP_TYPES = {"application/zip", "application/x-zip-compressed"}
_STATEMENT_TITLES: dict[StatementType, str] = {
    "income_statement": "Income statement",
    "balance_sheet": "Balance sheet",
    "cash_flow_statement": "Cash flow statement",
    "comprehensive_income": "Statement of comprehensive income",
    "changes_in_equity": "Statement of changes in equity",
}
_STANDARD_PREFIXES = {
    "ifrs-full",
    "uk-gaap",
    "uk-core",
    "frs-102",
    "jppfs_cor",
    "jpcrp_cor",
}


def extract_filing_financials(
    document: SourceDocument,
    filing: Filing,
) -> FilingFinancials:
    """Extract standardized statements from one supported filing document."""

    if _is_pdf(document):
        return extract_pdf_source_financials(
            document.data,
            filing,
            source_url=document.source_url,
            sha256=hashlib.sha256(document.data).hexdigest(),
        )
    if document.profile == "smv":
        return extract_pdf_table_financials(
            html_to_markdown(document.data),
            filing,
            source_url=document.source_url,
            sha256=hashlib.sha256(document.data).hexdigest(),
            extraction_method="smv-open-data-tables",
        )

    parsed = (
        parse_bmv_json_xbrl(document.data)
        if document.profile == "bmv-json"
        else parse_inline_xbrl(_inline_report(document, filing))
    )
    statements = _build_statements(parsed)
    if not statements:
        raise FinancialsUnavailableError(
            "The tagged filing contains no supported UK-GAAP, JP-GAAP, or IFRS "
            "statement facts."
        )
    return FilingFinancials(
        filing_id=filing.id,
        company_id=filing.company_id,
        source_url=document.source_url,
        statements=statements,
        fact_count=sum(
            fact.numeric and fact.value is not None for fact in parsed.facts
        ),
        taxonomy_namespaces=parsed.taxonomy_namespaces,
        sha256=hashlib.sha256(document.data).hexdigest(),
    )


def _inline_report(document: SourceDocument, filing: Filing) -> bytes:
    media_type = document.media_type.casefold()
    if media_type in _ZIP_TYPES or document.data.startswith(b"PK\x03\x04"):
        if filing.source == "edinet" or document.profile == "edinet":
            reports = html_documents_from_zip(document.data, public_documents_only=True)
            return b"\n".join(reports)
        return main_html_from_zip(document.data)
    if media_type in _HTML_TYPES or _looks_like_html(document.data):
        return document.data
    raise FinancialsUnavailableError(
        "Structured financials require an Inline XBRL XHTML or report package."
    )


def _build_statements(parsed: ParsedXbrl) -> tuple[FinancialStatement, ...]:
    candidates: dict[str, dict[str, list[XbrlFact]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for fact in parsed.facts:
        if not fact.numeric or fact.value is None:
            continue
        definition = definition_for_concept(fact.concept)
        context = parsed.contexts.get(fact.context_ref)
        if definition is None or context is None or _period(context) is None:
            continue
        candidates[definition.code][fact.concept].append(fact)

    line_items: dict[StatementType, list[FinancialLineItem]] = defaultdict(list)
    for definition in LINE_ITEMS:
        concepts = candidates.get(definition.code)
        if not concepts:
            continue
        concept, facts = max(
            concepts.items(),
            key=lambda item: _concept_score(item[0], item[1], parsed),
        )
        selected = _preferred_context_facts(facts, parsed.contexts)
        values = tuple(
            _financial_value(fact, parsed)
            for fact in sorted(
                selected,
                key=lambda fact: _fact_sort_key(fact, parsed.contexts),
                reverse=True,
            )
        )
        values = tuple(value for value in values if value is not None)
        if values:
            line_items[definition.statement_type].append(
                FinancialLineItem(
                    code=definition.code,
                    name=definition.name,
                    concept=concept,
                    values=values,
                )
            )

    statements: list[FinancialStatement] = []
    for statement_type in _STATEMENT_TITLES:
        items = tuple(line_items.get(statement_type, ()))
        if statement_type == "balance_sheet":
            items = _add_balance_sheet_totals(items)
        if not items:
            continue
        currency = _statement_currency(items)
        statements.append(
            FinancialStatement(
                statement_type=statement_type,
                title=_STATEMENT_TITLES[statement_type],
                currency=currency,
                line_items=items,
            )
        )
    return tuple(statements)


def _add_balance_sheet_totals(
    items: tuple[FinancialLineItem, ...],
) -> tuple[FinancialLineItem, ...]:
    by_code = {item.code: item for item in items}
    derived: list[FinancialLineItem] = []
    for code, name, left_code, right_code in (
        (
            "total_assets",
            "Total assets",
            "current_assets",
            "noncurrent_assets",
        ),
        (
            "total_liabilities",
            "Total liabilities",
            "current_liabilities",
            "noncurrent_liabilities",
        ),
    ):
        if code in by_code or left_code not in by_code or right_code not in by_code:
            continue
        values = _sum_line_items(by_code[left_code], by_code[right_code])
        if values:
            derived.append(
                FinancialLineItem(
                    code=code,
                    name=name,
                    concept=f"derived:{left_code}+{right_code}",
                    values=values,
                )
            )
    return items + tuple(derived)


def _sum_line_items(
    left: FinancialLineItem,
    right: FinancialLineItem,
) -> tuple[FinancialValue, ...]:
    right_values = {_financial_value_key(value): value for value in right.values}
    combined: list[FinancialValue] = []
    for left_value in left.values:
        right_value = right_values.get(_financial_value_key(left_value))
        if right_value is None or left_value.unit != right_value.unit:
            continue
        combined.append(
            left_value.model_copy(
                update={
                    "value": left_value.value + right_value.value,
                    "decimals": _less_precise(
                        left_value.decimals, right_value.decimals
                    ),
                }
            )
        )
    return tuple(combined)


def _financial_value_key(value: FinancialValue) -> tuple[object, ...]:
    return (
        value.period.start_date,
        value.period.end_date,
        value.period.kind,
        value.dimensions,
    )


def _less_precise(left: str | None, right: str | None) -> str | None:
    return left if _precision(left) <= _precision(right) else right


def _concept_score(
    concept: str,
    facts: list[XbrlFact],
    parsed: ParsedXbrl,
) -> tuple[int, int, int, int]:
    contexts = [
        parsed.contexts[fact.context_ref]
        for fact in facts
        if fact.context_ref in parsed.contexts
    ]
    dimensionless = sum(not context.dimensions for context in contexts)
    unique_periods = len(
        {
            (context.start_date, context.end_date, context.instant)
            for context in contexts
        }
    )
    prefix = concept.split(":", 1)[0].casefold() if ":" in concept else ""
    standard = int(prefix in _STANDARD_PREFIXES)
    return dimensionless, unique_periods, standard, len(facts)


def _preferred_context_facts(
    facts: list[XbrlFact], contexts: dict[str, XbrlContext]
) -> list[XbrlFact]:
    dimensionless = [
        fact
        for fact in facts
        if fact.context_ref in contexts and not contexts[fact.context_ref].dimensions
    ]
    pool = dimensionless or facts
    selected: dict[tuple[object, ...], XbrlFact] = {}
    for fact in pool:
        context = contexts.get(fact.context_ref)
        if context is None:
            continue
        key = (
            context.start_date,
            context.end_date,
            context.instant,
            context.dimensions,
            fact.unit_ref,
        )
        previous = selected.get(key)
        if previous is None or _precision(fact.decimals) > _precision(
            previous.decimals
        ):
            selected[key] = fact
    return list(selected.values())


def _financial_value(fact: XbrlFact, parsed: ParsedXbrl) -> FinancialValue | None:
    context = parsed.contexts.get(fact.context_ref)
    if context is None or fact.value is None:
        return None
    period = _period(context)
    if period is None:
        return None
    return FinancialValue(
        period=period,
        value=fact.value,
        unit=parsed.units.get(fact.unit_ref or "") or fact.unit_ref,
        decimals=fact.decimals,
        dimensions=context.dimensions,
    )


def _period(context: XbrlContext) -> ReportingPeriod | None:
    if context.instant is not None:
        return ReportingPeriod(
            id=context.id,
            end_date=context.instant,
            kind="instant",
            fiscal_period="instant",
        )
    if context.start_date is None or context.end_date is None:
        return None
    days = (context.end_date - context.start_date).days
    if 300 <= days <= 380:
        fiscal_period = "FY"
    elif 170 <= days <= 200:
        fiscal_period = "H1"
    elif 75 <= days <= 105:
        fiscal_period = "Q"
    elif 250 <= days <= 290:
        fiscal_period = "9M"
    else:
        fiscal_period = None
    return ReportingPeriod(
        id=context.id,
        start_date=context.start_date,
        end_date=context.end_date,
        kind="duration",
        fiscal_period=fiscal_period,
    )


def _fact_sort_key(
    fact: XbrlFact, contexts: dict[str, XbrlContext]
) -> tuple[date, date]:
    context = contexts[fact.context_ref]
    end = context.instant or context.end_date or date.min
    return end, context.start_date or end


def _statement_currency(items: tuple[FinancialLineItem, ...]) -> str | None:
    currencies: Counter[str] = Counter()
    for item in items:
        if item.code in {"basic_eps", "diluted_eps"}:
            continue
        for value in item.values:
            currency = _currency_code(value.unit)
            if currency:
                currencies[currency] += 1
    return currencies.most_common(1)[0][0] if currencies else None


def _currency_code(unit: str | None) -> str | None:
    if not unit:
        return None
    token = unit.split(" / ", 1)[0].rsplit(":", 1)[-1].upper()
    return token if len(token) == 3 and token.isalpha() else None


def _precision(decimals: str | None) -> int:
    if decimals is None:
        return -10_000
    if decimals.casefold() == "inf":
        return 10_000
    try:
        return int(decimals)
    except ValueError:
        return -10_000


def _looks_like_html(data: bytes) -> bool:
    prefix = data[:512].lstrip().lower()
    return prefix.startswith((b"<!doctype html", b"<html", b"<?xml"))


def _is_pdf(document: SourceDocument) -> bool:
    return (
        document.media_type.casefold() == "application/pdf"
        or document.data.startswith(b"%PDF")
    )
