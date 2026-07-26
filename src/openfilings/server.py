"""Token-bounded MCP tools backed by the OpenFilings application service."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from openfilings.exceptions import FinancialsUnavailableError, OpenFilingsError
from openfilings.mcp_support import (
    MAX_FINANCIAL_LINE_ITEMS,
    MAX_FINANCIAL_PERIODS,
    MAX_METADATA_RESULTS,
    MAX_OUTLINE_SECTIONS,
    MAX_SEARCH_RESULTS,
    MAX_SNIPPET_CHARS,
    company_facts_view,
    company_summary,
    failure,
    filing_summary,
    financials_view,
    insider_dealing_summary,
    major_holder_summary,
    query_excerpt,
    section_summary,
    success,
    text_window,
    validate_limit,
)
from openfilings.models import OcrMode, StatementType
from openfilings.service import OpenFilingsService
from openfilings.validation import validate_financials, validation_view

mcp = FastMCP(
    "OpenFilings",
    instructions=(
        "Search listed companies and public filings across supported global markets. "
        "Use companies_search and filings_list for discovery, filing_outline before "
        "reading content, filing_read for one section, and filing_search for short "
        "relevant excerpts. filing_markdown is paginated and should be a last resort. "
        "Tools are read-only except sedar_filing_import, which stores one "
        "user-selected public Canadian PDF in the local cache. Use "
        "data_quality_report before relying on extracted values, "
        "financials_query for a small set of facts, and filings_diff to "
        "compare financial facts between reports."
    ),
)


@mcp.tool()
async def companies_search(
    query: str,
    limit: int = 5,
    source: str = "all",
) -> dict[str, Any]:
    """Find companies using compact metadata; start most company workflows here."""

    try:
        validate_limit(limit, maximum=MAX_METADATA_RESULTS)
        async with OpenFilingsService.from_settings() as service:
            companies = await service.search_companies(
                query,
                limit=limit,
                source=source,
            )
        return success(
            {
                "companies": [company_summary(company) for company in companies],
                "count": len(companies),
            },
            next_steps=(
                "Use filings_list with a company id to discover recent filings.",
            ),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def filings_list(
    company_id: str,
    category: str | None = "accounts",
    limit: int = 10,
    source: str = "all",
    history_days: int = 120,
) -> dict[str, Any]:
    """List compact filing metadata without downloading document content."""

    try:
        validate_limit(limit, maximum=MAX_METADATA_RESULTS)
        async with OpenFilingsService.from_settings() as service:
            filings = await service.list_filings(
                company_id,
                category=category,
                limit=limit,
                source=source,
                edinet_lookback_days=history_days,
            )
        return success(
            {
                "filings": [filing_summary(filing) for filing in filings],
                "count": len(filings),
            },
            next_steps=(
                "Use filing_outline with a filing id before requesting content.",
                "Use filing_financials when structured statements answer the question.",
            ),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def disclosures_search(
    keyword: str,
    limit: int = 10,
    source: str = "all",
) -> dict[str, Any]:
    """Full-text search across every issuer's disclosures, not scoped to
    one company. Only fca_nsm (headline search) and cvm (its yearly filing
    index) support this."""

    try:
        validate_limit(limit, maximum=MAX_METADATA_RESULTS)
        async with OpenFilingsService.from_settings() as service:
            filings = await service.search_disclosures(
                keyword,
                limit=limit,
                source=source,
            )
        return success(
            {
                "filings": [filing_summary(filing) for filing in filings],
                "count": len(filings),
            },
            next_steps=(
                "Use filing_outline or filing_markdown with a filing id for content.",
            ),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def company_facts(
    company_id: str,
    periods: int = 8,
    source: str = "all",
    statements: list[StatementType] | None = None,
    detail: Literal["minimal", "standard", "full"] = "standard",
    max_line_items: int = 40,
) -> dict[str, Any]:
    """Merge a company's most recent structured filings into one multi-
    period fact series per line item, instead of one filing at a time."""

    try:
        async with OpenFilingsService.from_settings() as service:
            facts = await service.get_company_facts(
                company_id,
                periods=periods,
                source=source,
            )
        return success(
            company_facts_view(
                facts,
                statements=statements,
                periods=periods,
                detail=detail,
                max_line_items=max_line_items,
            ),
            next_steps=(
                "Reduce periods or line items when a smaller response is sufficient.",
            ),
        )
    except FinancialsUnavailableError as exc:
        return failure(str(exc), error_code=type(exc).__name__.upper())
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def data_quality_report(filing_id: str) -> dict[str, Any]:
    """Report extraction provenance, confidence, and validation for one filing."""

    try:
        async with OpenFilingsService.from_settings() as service:
            financials = await service.get_filing_financials(filing_id)
        values = [
            value
            for statement in financials.statements
            for item in statement.line_items
            for value in item.values
        ]
        by_provenance: dict[str, int] = {}
        for value in values:
            by_provenance[value.provenance] = by_provenance.get(value.provenance, 0) + 1
        return success(
            {
                "filing_id": financials.filing_id,
                "source_url": financials.source_url,
                "extraction_method": financials.extraction_method,
                "fact_count": financials.fact_count,
                "provenance_counts": by_provenance,
                "confidence": {
                    "minimum": (
                        min(value.confidence for value in values) if values else None
                    ),
                    "maximum": (
                        max(value.confidence for value in values) if values else None
                    ),
                },
                "validation": validation_view(validate_financials(financials)),
            },
            next_steps=(
                "Use filing_financials with detail='full' to inspect individual facts.",
            ),
        )
    except FinancialsUnavailableError as exc:
        return failure(str(exc), error_code=type(exc).__name__.upper())
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def financials_query(
    company_id: str,
    codes: list[str],
    periods: int = 4,
    source: str = "all",
) -> dict[str, Any]:
    """Return only selected multi-period financial facts with provenance."""

    try:
        validate_limit(periods, maximum=MAX_FINANCIAL_PERIODS, name="periods")
        if not codes or len(codes) > MAX_FINANCIAL_LINE_ITEMS:
            raise ValueError(
                f"codes must contain between 1 and {MAX_FINANCIAL_LINE_ITEMS} items."
            )
        wanted = {code.strip() for code in codes if code.strip()}
        if not wanted:
            raise ValueError("codes must contain at least one non-empty item.")
        async with OpenFilingsService.from_settings() as service:
            facts = await service.get_company_facts(
                company_id, periods=periods, source=source
            )
        matches = []
        for statement in facts.statements:
            for item in statement.line_items:
                if item.code not in wanted:
                    continue
                values = sorted(
                    item.values, key=lambda value: value.period.end_date, reverse=True
                )[:periods]
                matches.append(
                    {
                        "statement_type": statement.statement_type,
                        "code": item.code,
                        "name": item.name,
                        "concept": item.concept,
                        "values": [
                            {
                                "period": value.period.label,
                                "value": str(value.value),
                                "unit": value.unit,
                                "provenance": value.provenance,
                                "confidence": value.confidence,
                            }
                            for value in values
                        ],
                    }
                )
        return success(
            {"company_id": company_id, "facts": matches, "count": len(matches)}
        )
    except FinancialsUnavailableError as exc:
        return failure(str(exc), error_code=type(exc).__name__.upper())
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def historical_backfill(
    company_id: str,
    source: Literal["fca_nsm", "esef"],
    limit: int = 100,
) -> dict[str, Any]:
    """Download available UK/ESEF structured filing history into local storage."""

    try:
        validate_limit(limit, maximum=500)
        async with OpenFilingsService.from_settings() as service:
            result = await service.backfill_company_history(
                company_id, source=source, limit=limit
            )
        return success(
            result.model_dump(mode="json"),
            next_steps=(
                "Use historical_facts_query with view='as_reported', "
                "'latest_restated', or 'as_of'.",
            ),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def historical_facts_query(
    company_id: str,
    codes: list[str] | None = None,
    view: Literal["as_reported", "latest_restated", "as_of"] = "latest_restated",
    as_of: date | None = None,
    limit: int = 1_000,
) -> dict[str, Any]:
    """Query locally stored filing facts with restatement-aware semantics."""

    try:
        validate_limit(limit, maximum=10_000)
        if view == "as_of" and as_of is None:
            raise ValueError("as_of is required when view is as_of.")
        async with OpenFilingsService.from_settings() as service:
            facts = service.historical_facts(
                company_id,
                codes=codes,
                view=view,
                as_of=as_of,
                limit=limit,
            )
        return success(
            {
                "company_id": company_id,
                "view": view,
                "as_of": as_of.isoformat() if as_of else None,
                "facts": [fact.model_dump(mode="json") for fact in facts],
                "count": len(facts),
            },
            next_steps=(
                "Run historical_backfill first when this local history is empty.",
            ),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def company_research_brief(
    company_id: str, source: str = "all"
) -> dict[str, Any]:
    """Return recent filings and a compact three-period financial profile.

    Ownership and director-dealing data stay in their dedicated tools because
    coverage differs by source; missing data must not imply no activity.
    """
    try:
        async with OpenFilingsService.from_settings() as service:
            filings = await service.list_filings(company_id, source=source, limit=5)
            facts = await service.get_company_facts(
                company_id, source=source, periods=3
            )
        return success(
            {
                "company_id": company_id,
                "latest_filings": [filing_summary(filing) for filing in filings],
                "financials": company_facts_view(
                    facts,
                    statements=None,
                    periods=3,
                    detail="standard",
                    max_line_items=12,
                ),
                "caveats": [
                    "Verify investment-critical values against source filings.",
                    "Use major_holders_list and insider_dealings_list where their "
                    "source-market coverage applies.",
                ],
            }
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def companies_compare(
    company_ids: list[str], code: str, source: str = "all"
) -> dict[str, Any]:
    """Compare one normalized fact across two to twenty UK/ESEF issuers."""
    try:
        if not 2 <= len(company_ids) <= 20:
            raise ValueError("company_ids must contain between 2 and 20 items.")
        allowed = (
            "uk_",
            "nl_",
            "fr_",
            "es_",
            "it_",
            "dk_",
            "se_",
            "fi_",
            "no_",
            "pl_",
            "be_",
            "at_",
            "lu_",
            "pt_",
        )
        rows: list[dict[str, Any]] = []
        async with OpenFilingsService.from_settings() as service:
            for company_id in company_ids:
                if not company_id.startswith(allowed):
                    raise ValueError("companies_compare supports UK and ESEF ids only.")
                facts = await service.get_company_facts(
                    company_id, source=source, periods=4
                )
                values = [
                    value
                    for statement in facts.statements
                    for item in statement.line_items
                    if item.code == code
                    for value in item.values
                ]
                rows.append(
                    {
                        "company_id": company_id,
                        "values": [
                            {
                                "period": value.period.label,
                                "period_end": value.period.end_date.isoformat(),
                                "value": str(value.value),
                                "unit": value.unit,
                                "confidence": value.confidence,
                                "provenance": value.provenance,
                            }
                            for value in sorted(
                                values,
                                key=lambda item: item.period.end_date,
                                reverse=True,
                            )[:4]
                        ],
                    }
                )
        return success(
            {
                "metric": code,
                "companies": rows,
                "caveat": (
                    "Values remain in each issuer's reported currency; no FX "
                    "normalization is applied."
                ),
            }
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def filings_diff(first_filing_id: str, second_filing_id: str) -> dict[str, Any]:
    """Compare normalized facts in two filings and return changed values."""
    try:
        async with OpenFilingsService.from_settings() as service:
            first = await service.get_filing_financials(first_filing_id)
            second = await service.get_filing_financials(second_filing_id)

        def flatten(financials: Any) -> dict[tuple[str, str, str], dict[str, str]]:
            return {
                (statement.statement_type, item.code, value.period.label): {
                    "value": str(value.value),
                    "unit": value.unit,
                    "provenance": value.provenance,
                    "confidence": str(value.confidence),
                }
                for statement in financials.statements
                for item in statement.line_items
                for value in item.values
            }

        left, right = flatten(first), flatten(second)
        changes = [
            {
                "statement_type": key[0],
                "code": key[1],
                "period": key[2],
                "first": left.get(key),
                "second": right.get(key),
            }
            for key in sorted(set(left) | set(right))
            if left.get(key) != right.get(key)
        ]
        return success(
            {
                "first_filing_id": first_filing_id,
                "second_filing_id": second_filing_id,
                "changes": changes[:100],
                "truncated": len(changes) > 100,
            }
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def watchlist_check(
    company_ids: list[str], since: date, source: str = "all"
) -> dict[str, Any]:
    """Statelessly return filings published on or after ``since``."""
    try:
        if not 1 <= len(company_ids) <= 50:
            raise ValueError("company_ids must contain between 1 and 50 items.")
        updates: dict[str, list[dict[str, Any]]] = {}
        async with OpenFilingsService.from_settings() as service:
            for company_id in company_ids:
                filings = await service.list_filings(
                    company_id, source=source, category=None, limit=50
                )
                updates[company_id] = [
                    filing_summary(filing)
                    for filing in filings
                    if filing.filing_date >= since
                ]
        return success(
            {
                "since": since.isoformat(),
                "updates": updates,
                "caveat": (
                    "This is stateless and reports published disclosures. Insider "
                    "and ownership events appear only where a source publishes "
                    "them as filings."
                ),
            }
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def major_holders_list(
    company_id: str,
    limit: int = 25,
) -> dict[str, Any]:
    """List structured major-holder records for a UK or Brazilian issuer."""

    try:
        validate_limit(limit, maximum=MAX_METADATA_RESULTS)
        async with OpenFilingsService.from_settings() as service:
            holders = await service.list_major_holders(company_id, limit=limit)
        return success(
            {
                "holders": [major_holder_summary(holder) for holder in holders],
                "count": len(holders),
            }
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def major_holders_search(
    holder_name: str,
    scan_limit: int = 200,
    limit: int = 25,
) -> dict[str, Any]:
    """Search holder positions across UK notifications and Brazilian FRE data."""

    try:
        validate_limit(limit, maximum=MAX_METADATA_RESULTS)
        async with OpenFilingsService.from_settings() as service:
            holders = await service.search_major_holders(
                holder_name,
                scan_limit=scan_limit,
                limit=limit,
            )
        return success(
            {
                "holders": [major_holder_summary(holder) for holder in holders],
                "count": len(holders),
                "scan_limit": scan_limit,
            },
            next_steps=(
                "Increase scan_limit to search further back if nothing was found.",
            ),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def insider_dealings_list(
    company_id: str,
    limit: int = 25,
) -> dict[str, Any]:
    """List UK MAR director/PDMR/PCA dealings parsed into structured fields."""

    try:
        validate_limit(limit, maximum=MAX_METADATA_RESULTS)
        async with OpenFilingsService.from_settings() as service:
            dealings = await service.list_insider_dealings(
                company_id,
                limit=limit,
            )
        return success(
            {
                "dealings": [insider_dealing_summary(dealing) for dealing in dealings],
                "count": len(dealings),
            }
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def sedar_filing_import(
    company_id: str,
    document_url: str,
    title: str,
    filing_date: date,
    period_end: date | None = None,
    filing_type: str = "annual",
    category: str = "accounts",
) -> dict[str, Any]:
    """Import one official SEDAR+ generated PDF URL into the local cache."""

    try:
        async with OpenFilingsService.from_settings() as service:
            filing = await service.import_sedar_filing(
                company_id,
                document_url=document_url,
                title=title,
                filing_date=filing_date,
                period_end=period_end,
                filing_type=filing_type,
                category=category,
            )
        return success(
            {"filing": filing_summary(filing.record)},
            next_steps=(
                "Use filing_outline with the returned filing id.",
                "Use filing_financials when structured statements are needed.",
            ),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def filing_outline(
    filing_id: str,
    limit: int = 100,
    refresh: bool = False,
) -> dict[str, Any]:
    """Return headings and sizes only, allowing targeted section selection."""

    try:
        validate_limit(limit, maximum=MAX_OUTLINE_SECTIONS)
        async with OpenFilingsService.from_settings() as service:
            document = await service.get_filing_document(
                filing_id,
                refresh=refresh,
            )
        selected = document.sections[:limit]
        return success(
            {
                "filing_id": filing_id,
                "sections": [section_summary(section) for section in selected],
                "section_count": len(document.sections),
                "truncated": len(selected) < len(document.sections),
            },
            next_steps=(
                "Use filing_read with one section title.",
                "Use filing_search when the relevant section is unknown.",
            ),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def filing_sections(
    filing_id: str,
    query: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Compatibility outline tool; returns headings without section bodies."""

    try:
        validate_limit(limit, maximum=MAX_OUTLINE_SECTIONS)
        async with OpenFilingsService.from_settings() as service:
            document = await service.get_filing_document(filing_id)
        sections = (
            document.search(query, limit=limit) if query else document.sections[:limit]
        )
        return success(
            {
                "filing_id": filing_id,
                "sections": [section_summary(section) for section in sections],
                "count": len(sections),
            },
            next_steps=("Use filing_read to retrieve one selected section.",),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def filing_read(
    filing_id: str,
    section: str,
    offset: int = 0,
    max_chars: int = 6_000,
    refresh: bool = False,
) -> dict[str, Any]:
    """Read one named filing section through a bounded, paginated response."""

    try:
        async with OpenFilingsService.from_settings() as service:
            document = await service.get_filing_document(
                filing_id,
                refresh=refresh,
            )
        selected = document.section(section)
        if selected is None:
            available = [item.title for item in document.sections[:20]]
            return failure(
                f"No section matched {section!r}.",
                error_code="SECTION_NOT_FOUND",
                suggestions=[f"Available sections: {', '.join(available)}"],
            )
        window = text_window(selected.markdown, offset=offset, max_chars=max_chars)
        return success(
            {
                "filing_id": filing_id,
                "title": selected.title,
                "level": selected.level,
                "start_line": selected.start_line,
                "markdown": window.text,
                **window.metadata(),
            },
            next_steps=_pagination_step(window.next_offset, "filing_read"),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def filing_search(
    filing_id: str,
    query: str,
    limit: int = 5,
    snippet_chars: int = 1_200,
) -> dict[str, Any]:
    """Search one filing and return ranked, bounded excerpts."""

    try:
        validate_limit(limit, maximum=MAX_SEARCH_RESULTS)
        validate_limit(
            snippet_chars,
            maximum=MAX_SNIPPET_CHARS,
            name="snippet_chars",
        )
        async with OpenFilingsService.from_settings() as service:
            document = await service.get_filing_document(filing_id)
        matches = document.ranked_search(query, limit=limit)
        results = [
            {
                **section_summary(match.section),
                "score": match.score,
                "matched_terms": list(match.matched_terms),
                "snippet": query_excerpt(
                    match.section.text,
                    query,
                    max_chars=snippet_chars,
                ),
            }
            for match in matches
        ]
        return success(
            {
                "filing_id": filing_id,
                "query": query,
                "results": results,
                "count": len(results),
            },
            next_steps=("Use filing_read with a result title for more context.",),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def filing_markdown(
    filing_id: str,
    offset: int = 0,
    max_chars: int = 12_000,
    refresh: bool = False,
    ocr_mode: OcrMode | None = None,
) -> dict[str, Any]:
    """Read a bounded Markdown page; prefer outline, search, or section tools."""

    try:
        async with OpenFilingsService.from_settings() as service:
            content = await service.get_filing_markdown(
                filing_id,
                refresh=refresh,
                ocr_mode=ocr_mode,
            )
        window = text_window(content.markdown, offset=offset, max_chars=max_chars)
        return success(
            {
                "filing_id": filing_id,
                "markdown": window.text,
                **window.metadata(),
                "source_url": content.source_url,
                "media_type": content.media_type,
                "extraction_method": content.extraction_method,
                "quality": {
                    "score": content.quality.score,
                    "status": content.quality.status,
                    "warnings": list(content.quality.warnings),
                },
                "from_cache": content.from_cache,
            },
            next_steps=_pagination_step(window.next_offset, "filing_markdown"),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


@mcp.tool()
async def filing_financials(
    filing_id: str,
    statements: list[StatementType] | None = None,
    periods: int = 4,
    detail: Literal["minimal", "standard", "full"] = "standard",
    max_line_items: int = 40,
    refresh: bool = False,
) -> dict[str, Any]:
    """Return selected statements with bounded periods and line items."""

    try:
        validate_limit(periods, maximum=MAX_FINANCIAL_PERIODS, name="periods")
        validate_limit(
            max_line_items,
            maximum=MAX_FINANCIAL_LINE_ITEMS,
            name="max_line_items",
        )
        async with OpenFilingsService.from_settings() as service:
            financials = await service.get_filing_financials(
                filing_id,
                refresh=refresh,
            )
        view = financials_view(
            financials,
            statements=statements,
            periods=periods,
            detail=detail,
            max_line_items=max_line_items,
        )
        next_steps = [
            "Request only the needed statement and reduce periods or line items "
            "when a smaller response is sufficient.",
        ]
        validation = view.get("validation")
        if validation is not None and not validation["ok"]:
            next_steps.append(
                "The extracted statements failed a deterministic accounting-"
                "identity check (see 'validation.findings') - this figure may "
                "be wrong, not just incomplete. Verify with filing_search or "
                "filing_markdown before relying on it."
            )
        return success(view, next_steps=next_steps)
    except FinancialsUnavailableError as exc:
        return failure(
            str(exc),
            error_code=type(exc).__name__.upper(),
            suggestions=(
                "Structured extraction failed - the filing's own text is "
                "still readable. Call filing_search with a query like "
                "'total assets total liabilities total equity' (or "
                "'revenue net income' for the income statement) to jump "
                "straight to the relevant pages, or filing_markdown to "
                "read the full converted text and extract the figures "
                "directly.",
            ),
        )
    except (OpenFilingsError, ValueError) as exc:
        return _request_failure(exc)


def _request_failure(exc: Exception) -> dict[str, Any]:
    return failure(
        str(exc),
        error_code=type(exc).__name__.upper(),
        suggestions=(
            "Check identifiers and parameter limits.",
            "Use companies_search and filings_list to resolve stable ids.",
        ),
    )


def _pagination_step(next_offset: int | None, tool_name: str) -> tuple[str, ...]:
    if next_offset is None:
        return ()
    return (f"Call {tool_name} again with offset={next_offset} to continue.",)


def run() -> None:
    mcp.run(transport="stdio")
