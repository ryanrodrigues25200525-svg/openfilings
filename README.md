# OpenFilings

OpenFilings is a lightweight, local-first tool for searching UK companies and
listed issuers, listing official filings, and converting source documents into
Markdown for LLMs.

## What works

- Search Companies House and the FCA National Storage Mechanism (NSM)
- Resolve listed-company names to legal entity identifiers (LEIs)
- Merge matching Companies House and FCA identities
- List statutory filings and regulated disclosures in one timeline
- Download public PDF, HTML/XHTML, and tagged-report ZIP documents
- Prefer Companies House tagged XHTML accounts over PDF when available
- Convert documents locally to Markdown without retaining the originals
- Navigate extracted documents by heading and search within sections
- Extract standardized income, balance-sheet, cash-flow, and comprehensive
  income statements from UK-GAAP and ESEF/IFRS Inline XBRL
- Score extraction quality with explainable warnings
- Optionally route scanned PDFs through page-at-a-time Tesseract OCR
- Reuse compressed Markdown and duplicate content through a SQLite cache
- Enforce a logical cache limit and reclaim space with a cleanup command
- Use the same operations from the CLI or an MCP server

The FCA-only path is free and needs no API key. Companies House also provides a
free API, but requires you to create an API key.

## Setup

```bash
uv sync
```

To include Companies House, expose its API key:

```bash
export COMPANIES_HOUSE_API_KEY="your-key"
```

Never commit the key. `.env` is ignored, but OpenFilings intentionally does not
load dotenv files implicitly.

## CLI

FCA-only usage works immediately:

```bash
uv run openfilings search "Tesco" --source fca-nsm
uv run openfilings filings uk_lei_2138002P5RNKC5W2JZ46 --source fca-nsm
uv run openfilings fetch uk_nsm_1cc57f6a-e707-4fe8-a137-04731cb7c217
uv run openfilings financials uk_nsm_NI-000144970 -o tesco-financials.json
uv run openfilings sections uk_nsm_NI-000144970 --query revenue
```

Each fetched document includes its extraction method and quality score. OCR
defaults to `auto`: it runs only when native PDF extraction is unusable and a
system Tesseract executable is available. Override it per request with
`--ocr never` or `--ocr always`.

With a Companies House key, the default `all` source searches both systems,
merges matching names, and remembers the LEI for later combined listing:

```bash
uv run openfilings search "Tesco"
uv run openfilings filings uk_00445790 --limit 50
```

Available source values are `all`, `companies-house`, and `fca-nsm`. Use
`--output report.md` with `fetch` to save Markdown. Set
`OPENFILINGS_DATA_DIR` to move the SQLite cache; it defaults to `.openfilings`
in the current directory.

Inspect and benchmark a local document without adding it to the cache:

```bash
uv run openfilings inspect-document annual-report.pdf
uv run openfilings inspect-document scan.pdf --ocr always -o scan.md
```

Inspect or prune the cache:

```bash
uv run openfilings cache status
uv run openfilings cache prune --max-mb 512
```

## Python API

The public API follows EdgarTools' collection-first ergonomics without importing
its SEC-specific runtime:

```python
from openfilings import OpenFilingsService

async with OpenFilingsService.from_settings() as service:
    filings = await service.filings(
        "uk_lei_2138002P5RNKC5W2JZ46",
        source="fca_nsm",
        limit=100,
    )
    annual_report = filings.filter(filing_type="ACS").latest()
    document = await service.get_filing_document(annual_report.id)
    financials = await service.get_filing_financials(annual_report.id)
```

`Filings` supports `filter`, `latest`, and `head`. `FilingDocument` exposes
heading-based sections plus local section search. Structured values preserve
their source concept, period, unit, decimals, and dimensions.

## MCP tools

- `companies_search(query, limit=10, source="all")`
- `filings_list(company_id, category="accounts", limit=25, source="all")`
- `filing_markdown(filing_id, refresh=False, ocr_mode=None)`
- `filing_sections(filing_id, query=None, limit=20)`
- `filing_financials(filing_id, refresh=False)`

Start the stdio server with `uv run openfilings serve`.

## Resource footprint

The complete development environment is approximately 129 MB. Normal search
and listing operations use only HTTP plus SQLite. PDF extraction loads native
PDF libraries only when required; HTML extraction is lightweight and local.
Source documents are discarded after conversion, while Markdown and normalized
financials are compressed. Inline XBRL is processed with a bounded streaming
parser, avoiding a full DOM for large annual reports. Tesseract is an optional
system executable and adds nothing to the Python environment when it is not
installed.

On Tesco's 2026 FCA ESEF filing (29.5 MB Inline XBRL), the structured parser ran
in about 0.63 seconds with approximately 150 MB peak resident memory on the
development machine. Results vary by filing and platform.

Relevant environment settings:

```bash
export OPENFILINGS_OCR_MODE=auto       # auto, never, or always
export OPENFILINGS_OCR_LANGUAGE=eng    # eng+fra for multiple installed packs
export OPENFILINGS_OCR_DPI=200
export OPENFILINGS_OCR_MAX_PAGES=250
export OPENFILINGS_CACHE_MAX_MB=512
```

## Source and licensing notes

The FCA connector uses the same public read-only search endpoint as the NSM web
application. The endpoint is not published as a separately versioned consumer
API, so its alias and response schema are isolated in the FCA adapter. Requests
are user-triggered, bounded, and do not crawl in the background.

NSM materials remain subject to the FCA's terms and the rights attached to each
filed document. PyMuPDF4LLM and PyMuPDF are AGPL-3.0 licensed; review their
licensing requirements before proprietary distribution.

The public API and parsing architecture were informed by the MIT-licensed
[EdgarTools](https://github.com/dgunning/edgartools). OpenFilings does not bundle
EdgarTools or its SEC, Pandas, and PyArrow runtime. See
`THIRD_PARTY_NOTICES.md`.

The lightweight Inline XBRL path extracts the normalized values used by the
application. Use [Arelle](https://arelle.org/) separately when standards-complete
taxonomy loading or regulatory conformance validation is required.
