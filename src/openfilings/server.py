"""MCP tools backed by the same OpenFilings service as the CLI."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from openfilings.models import OcrMode
from openfilings.service import OpenFilingsService

mcp = FastMCP(
    "OpenFilings",
    instructions=(
        "Search UK, Japanese, European, Brazilian, Taiwanese, Hong Kong, and "
        "Singapore-listed companies; list FCA NSM, EDINET, ESEF, CVM, TWSE/MOPS, "
        "HKEXnews, and SGX filings; and retrieve documents as Markdown. "
        "All tools are read-only."
    ),
)


@mcp.tool()
async def companies_search(
    query: str, limit: int = 10, source: str = "all"
) -> list[dict[str, Any]]:
    """Search supported markets and return stable OpenFilings company IDs."""

    async with OpenFilingsService.from_settings() as service:
        companies = await service.search_companies(query, limit=limit, source=source)
    return [company.model_dump(mode="json") for company in companies]


@mcp.tool()
async def filings_list(
    company_id: str,
    category: str | None = "accounts",
    limit: int = 25,
    source: str = "all",
    history_days: int = 120,
) -> list[dict[str, Any]]:
    """List normalized filings; history_days controls the EDINET lookback."""

    async with OpenFilingsService.from_settings() as service:
        filings = await service.list_filings(
            company_id,
            category=category,
            limit=limit,
            source=source,
            edinet_lookback_days=history_days,
        )
    return [filing.model_dump(mode="json") for filing in filings]


@mcp.tool()
async def filing_markdown(
    filing_id: str,
    refresh: bool = False,
    ocr_mode: OcrMode | None = None,
) -> dict[str, Any]:
    """Retrieve one filing as Markdown, using the local cache when possible."""

    async with OpenFilingsService.from_settings() as service:
        content = await service.get_filing_markdown(
            filing_id,
            refresh=refresh,
            ocr_mode=ocr_mode,
        )
    return content.model_dump(mode="json")


@mcp.tool()
async def filing_sections(
    filing_id: str,
    query: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List or search navigable Markdown sections in one filing."""

    async with OpenFilingsService.from_settings() as service:
        document = await service.get_filing_document(filing_id)
    sections = (
        document.search(query, limit=limit) if query else document.sections[:limit]
    )
    return [
        {
            "title": section.title,
            "level": section.level,
            "start_line": section.start_line,
            "markdown": section.markdown,
        }
        for section in sections
    ]


@mcp.tool()
async def filing_financials(
    filing_id: str,
    refresh: bool = False,
) -> dict[str, Any]:
    """Extract normalized statements from tagged filings or supported PDFs."""

    async with OpenFilingsService.from_settings() as service:
        financials = await service.get_filing_financials(
            filing_id,
            refresh=refresh,
        )
    return financials.model_dump(mode="json")


def run() -> None:
    mcp.run(transport="stdio")
