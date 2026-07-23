# OpenFilings Project Description

## Overview

OpenFilings is an open-source, local-first Python toolkit for public-company
filings outside the United States. It gives developers, analysts, and LLM agents
one consistent way to:

- search exchange-listed companies;
- list annual, interim, and other financial filings;
- retrieve public filing documents;
- convert PDF, HTML, XHTML, and tagged ZIP reports to Markdown;
- navigate reports by section and ranked text search;
- extract normalized financial statements; and
- cache processed results for fast offline reuse.

The project is inspired by EdgarTools' developer experience, not its
SEC-specific implementation. OpenFilings applies similar collection-first
Python ergonomics to regulators and exchanges across Europe, Asia, and the
Americas.

## Problem

Public filings outside the US are fragmented across regulators, exchanges, and
document formats. Each source has different identifiers, search behavior,
report metadata, access constraints, and filing layouts. A user who wants to
work across markets otherwise has to maintain separate integrations and parsing
pipelines.

OpenFilings isolates those differences in market adapters and returns a small
set of normalized, immutable models. The same workflow then works through
Python, a command-line interface, or an MCP server.

## Scope

OpenFilings is:

- a Python library;
- a CLI;
- a local stdio MCP server;
- a set of public-market source adapters;
- a local document and financial extraction pipeline; and
- a compressed SQLite cache.

OpenFilings is not:

- a US SEC/EDGAR client;
- a general private-company register;
- a consumer web application;
- a hosted multi-user database;
- a portfolio, trading, or valuation system;
- a standards-validating XBRL processor; or
- a replacement for the regulator's legal record.

## Supported markets

The current release covers 19 jurisdictions.

| Region | Jurisdiction | Source | Access status |
|---|---|---|---|
| Europe | United Kingdom | FCA NSM | Keyless |
| Europe | Netherlands | ESEF via filings.xbrl.org | Keyless |
| Europe | France | ESEF via filings.xbrl.org | Keyless |
| Europe | Spain | ESEF via filings.xbrl.org | Keyless |
| Europe | Italy | ESEF via filings.xbrl.org | Keyless |
| Europe | Denmark | ESEF via filings.xbrl.org | Keyless |
| Europe | Sweden | ESEF via filings.xbrl.org | Keyless |
| Europe | Finland | ESEF via filings.xbrl.org | Keyless |
| Asia | Japan | EDINET | Search is keyless; filing API needs a free key |
| Asia | Taiwan | TWSE/MOPS | Keyless |
| Asia | Hong Kong | HKEX/HKEXnews | Keyless |
| Asia | Singapore | SGX | Keyless |
| Asia | India | NSE | Keyless |
| Asia | Mainland China | SSE/CNINFO | Keyless |
| Americas | Brazil | CVM | Keyless |
| Americas | Mexico | BMV | Keyless |
| Americas | Canada | TSX/TSXV and SEDAR+ | Keyless discovery; explicit document import |
| Americas | Peru | SMV | Keyless |
| Americas | Colombia | SFC/SIMEV | Keyless |

The company universe is intentionally restricted to public operating companies
listed on supported stock exchanges. Funds, warrants, ETFs, alternate currency
counters, and similar non-company products are filtered where the upstream
source permits.

## Main capabilities

### Company and filing discovery

Search by company name, ticker, local code, or regulator identifier. OpenFilings
normalizes source records into stable company and filing IDs, caches metadata,
and can search previously cached companies and filings offline.

### Filing-to-Markdown conversion

OpenFilings handles:

- native and image-only PDFs;
- HTML and XHTML;
- Inline XBRL reports; and
- bounded filing ZIP archives.

Native extraction is attempted first. Quality is scored with explainable
warnings, and unusable PDFs can fall back to page-bounded Tesseract OCR when it
is installed.

### Financial statements

The structured layer preserves reporting periods, source concepts, values,
currencies or units, decimals, and dimensions. It normalizes five statement
families:

- income statement;
- balance sheet;
- cash-flow statement;
- comprehensive income; and
- changes in equity.

Statements can be converted to records or Markdown. Pandas support is an
optional dependency.

### LLM-friendly MCP

The MCP interface is designed to avoid sending entire annual reports into the
model context. An agent can discover metadata, inspect a section outline, read
one section, search for ranked excerpts, or request only selected statements
and periods. Large text is capped and paginated.

### Local cache

Company metadata, filing metadata, processed Markdown, and normalized
financials are stored in SQLite. Large payloads are compressed and governed by
a configurable cache budget. Original documents are discarded after processing
except for explicit SEDAR+ imports.

## Quick start

Install the locked development environment:

```bash
uv sync
```

Search a supported market:

```bash
uv run openfilings search "Tesco" --source fca-nsm
uv run openfilings search "Nokia" --source esef
uv run openfilings search "AMX" --source bmv
```

Use the returned stable ID to list and process filings:

```bash
uv run openfilings filings COMPANY_ID --source SOURCE
uv run openfilings fetch FILING_ID --output report.md
uv run openfilings financials FILING_ID --output financials.json
uv run openfilings sections FILING_ID --query revenue
```

Start the local MCP server:

```bash
uv run openfilings serve
```

Run these commands from the repository directory, or install the package so
the `openfilings` console command is available in another directory.

## Python example

```python
from openfilings import OpenFilings


async def inspect_company() -> None:
    async with OpenFilings.from_settings() as openfilings:
        company = await openfilings.company("Nokia", source="esef")
        filings = await company.get_filings(source="esef", limit=20)
        filing = filings.latest()
        if filing is None:
            return

        matches = await filing.search("revenue operating profit")
        financials = await filing.financials()

        for match in matches:
            print(match.section.title, match.score)
        if financials.income_statement() is not None:
            print(financials.income_statement().to_markdown())
```

## Technology

- Python 3.11 or newer
- `httpx` for asynchronous HTTP
- Pydantic for immutable normalized models
- Typer for the CLI
- FastMCP for local LLM tools
- SQLite and zlib for local persistence
- PyMuPDF4LLM for native PDF extraction
- Markdownify for HTML conversion
- optional Tesseract for OCR
- optional pandas for DataFrame output

The package does not require a server runtime, Redis, PostgreSQL, Pandas, or
PyArrow for normal use.

## Reliability and safety

OpenFilings applies bounded timeouts, retries, response sizes, archive expansion,
page counts, concurrency, cache size, and MCP output. Adapter-specific host
allowlists prevent document redirects from escaping trusted official domains.
Secrets are read from environment variables and are not persisted.

Deterministic fixture-based tests run separately from scheduled live regulator
smoke tests. CI also checks supported Python versions, package builds, installed
artifacts, source formatting, static security analysis, and locked
dependencies.

## Current maturity

The package is at version 0.21.0. Its normalized API, CLI, cache, extraction
pipeline, and MCP tools are implemented and covered by automated tests.
Real-world availability still depends on upstream regulator services, some of
which expose undocumented or browser-oriented contracts that can change.

The most important known constraints are:

- an EDINET key is required to retrieve Japanese filing history and documents;
- SEDAR+ filing discovery remains a manual browser step;
- ESEF coverage can lag the underlying national regulator;
- PDF statement extraction is heuristic and intentionally refuses low-confidence
  normalization; and
- remote HTTP MCP hosting and authentication are outside the current scope.

## Repository guide

| Path | Purpose |
|---|---|
| `src/openfilings/adapters/` | Market connectors |
| `src/openfilings/extraction/` | Document conversion and quality |
| `src/openfilings/xbrl/` | Financial and Inline XBRL normalization |
| `src/openfilings/storage/` | SQLite cache |
| `src/openfilings/service.py` | Shared application orchestration |
| `src/openfilings/resources.py` | High-level Python resources |
| `src/openfilings/cli.py` | Command-line interface |
| `src/openfilings/server.py` | MCP tools |
| `tests/` | Offline test suite and fixtures |
| `docs/architecture/` | Architecture decision records |

See `ARCHITECTURE.md` for the component and data-flow design, `README.md` for
complete usage and source notes, and `CHANGELOG.md` for release history.
