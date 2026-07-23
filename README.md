# OpenFilings

OpenFilings is a lightweight, local-first tool for searching listed companies
across Europe, Asia, and the Americas, listing public filings, and converting
source documents into Markdown for LLMs.

## Documentation

- [Project description](PROJECT_DESCRIPTION.md)
- [Architecture](ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## What works

- Search UK-listed issuers through the FCA National Storage Mechanism (NSM)
- Search Japanese filers by name, ticker, or EDINET code
- Search Netherlands, French, Spanish, Italian, Danish, Swedish, and Finnish
  ESEF issuers without a key
- Search active Brazilian exchange-listed issuers from the official CVM register
- List and download CVM annual and interim financial statements without a key
- Search current SGX Mainboard and Catalist companies while excluding non-stock products
- List and download SGX annual reports without a key
- Search BMV, NSE, Peruvian, and Colombian listed issuers
- List and download their public annual or interim financial reports without a key
- Search TSX and TSXV operating companies through the official TSX directory
- Import a user-selected SEDAR+ generated URL or browser-downloaded Canadian PDF
- List and download keyless Inline XBRL financial reports from filings.xbrl.org
- List EDINET annual, semiannual, quarterly, and current reports
- Resolve listed-company names to legal entity identifiers (LEIs)
- List regulated disclosures in one timeline
- Download public PDF, HTML/XHTML, and tagged-report ZIP documents
- Prefer tagged XHTML annual reports over PDF when available
- Convert documents locally to Markdown; retain originals only for explicit
  SEDAR+ imports so they remain usable without browser automation
- Navigate extracted documents by heading and search within sections
- Extract standardized income, balance-sheet, cash-flow, and comprehensive
  income statements from UK-GAAP, ESEF/IFRS, and EDINET Inline XBRL
- Read Brazil's normalized statements directly from CVM's Open Data DFP/ITR
  datasets - a standardized chart of accounts, not PDF parsing
- Derive high-confidence normalized statements from aligned SGX PDF tables
  (and CVM as a fallback when a filing isn't in the open dataset) while
  preserving labels, periods, currencies, and scale
- Score extraction quality with explainable warnings
- Optionally route scanned PDFs through page-at-a-time Tesseract OCR
- Reuse compressed Markdown and duplicate content through a SQLite cache
- Enforce a logical cache limit and reclaim space with a cleanup command
- Use the same operations from the CLI or an MCP server

All filing-feed families are free. FCA, European ESEF, CVM, SGX, BMV, NSE, SMV,
SFC, and TSX company discovery need no key. EDINET filing retrieval requires
free API-key registration. SEDAR+ discovery remains browser-based, but a
generated public document URL or locally downloaded PDF can be imported without
an account, API key, or browser runtime.

## Setup

```bash
uv sync
```

Expose the keys for the sources you want to use:

```bash
export EDINET_API_KEY="your-key"
```

Never commit the key. `.env` is ignored, but OpenFilings intentionally does not
load dotenv files implicitly.

Register through the [EDINET API registration
page](https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1).

## CLI

UK-listed company usage works immediately:

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

The default `all` source searches every configured listed-company market:

```bash
uv run openfilings search "Tesco"
uv run openfilings filings uk_lei_2138002P5RNKC5W2JZ46 --limit 50
```

Japanese company search works without a key. Filing history and download use
EDINET API v2 and require `EDINET_API_KEY`:

```bash
uv run openfilings search "Sony" --source edinet
uv run openfilings filings jp_E01777 --source edinet --history-days 120
uv run openfilings fetch jp_edinet_S1000001 -o sony-report.md
uv run openfilings financials jp_edinet_S1000001 -o sony-financials.json
```

Netherlands company search, filing history, Markdown, and structured IFRS
financials work without registration:

```bash
uv run openfilings search "ASML" --source esef
uv run openfilings filings nl_lei_724500Y6DUVHQD6OXN27 --source esef
uv run openfilings fetch nl_esef_23718 -o asml-report.md
uv run openfilings financials nl_esef_23718 -o asml-financials.json
```

France uses the same keyless ESEF path:

```bash
uv run openfilings search "TotalEnergies" --source esef
uv run openfilings filings fr_lei_529900S21EQ1BO4ESM68 --source esef
uv run openfilings fetch fr_esef_24364 -o totalenergies-report.md
uv run openfilings financials fr_esef_24364 -o totalenergies-financials.json
```

Spain is available through the same commands and `es_lei_...` IDs:

```bash
uv run openfilings search "Iberdrola" --source esef
uv run openfilings filings es_lei_5QK37QC7NWOJ8D7WVQ45 --source esef
uv run openfilings fetch es_esef_18556 -o iberdrola-report.md
uv run openfilings financials es_esef_18556 -o iberdrola-financials.json
```

Italy uses `it_lei_...` IDs and the same keyless pipeline:

```bash
uv run openfilings search "Enel" --source esef
uv run openfilings filings it_lei_WOCMU6HCI0OJWNPRZS33 --source esef
uv run openfilings fetch it_esef_18316 -o enel-report.md
uv run openfilings financials it_esef_18316 -o enel-financials.json
```

Denmark uses `dk_lei_...` IDs. The public index includes annual and interim
reports, so select the year-end filing when annual financials are required:

```bash
uv run openfilings search "Novo Nordisk" --source esef
uv run openfilings filings dk_lei_549300DAQ1CVT6CXN342 --source esef
uv run openfilings fetch dk_esef_24266 -o novo-nordisk-report.md
uv run openfilings financials dk_esef_24266 -o novo-nordisk-financials.json
```

Sweden uses `se_lei_...` IDs and the same keyless pipeline:

```bash
uv run openfilings search "Ericsson" --source esef
uv run openfilings filings se_lei_549300W9JLPW15XIFM52 --source esef
uv run openfilings fetch se_esef_19170 -o ericsson-report.md
uv run openfilings financials se_esef_19170 -o ericsson-financials.json
```

Finland uses `fi_lei_...` IDs and the same keyless pipeline:

```bash
uv run openfilings search "Nokia" --source esef
uv run openfilings filings fi_lei_549300A0JPRWG1KI7U06 --source esef
uv run openfilings fetch fi_esef_23894 -o nokia-report.md
uv run openfilings financials fi_esef_23894 -o nokia-financials.json
```

Brazil uses the official keyless CVM company register and IPE document archive.
Only active `BOLSA` issuers are returned. CVM reports are PDFs for Markdown and
document reading, but `financials` reads structured statement rows directly
from CVM's Open Data DFP/ITR datasets when the company and year are covered,
falling back to PDF-derived tables otherwise - both need no key:

```bash
uv run openfilings search "Banco do Brasil" --source cvm
uv run openfilings filings br_cvm_001023 --source cvm
uv run openfilings fetch br_cvm_1046308 -o banco-do-brasil-report.md
uv run openfilings financials br_cvm_1046308 -o banco-do-brasil-financials.json
```

Singapore uses SGX's keyless stocks, market-metadata, and financial-reports
feeds. Only Mainboard and Catalist stock counters are joined to issuer records;
GlobalQuote counters and non-stock exchange products are excluded:

```bash
uv run openfilings search "S68" --source sgx
uv run openfilings filings sg_sgx_1J26 --source sgx
uv run openfilings fetch sg_sgx_2J4PCEOQYA3WTBWP -o sgx-report.md
uv run openfilings financials sg_sgx_2J4PCEOQYA3WTBWP -o sgx-financials.json
```

Mexico, India, Peru, and Colombia use keyless official exchange or regulator
data. Each example begins with a live company search; pass the returned ID to
`filings`:

```bash
uv run openfilings search "AMX" --source bmv
uv run openfilings search "RELIANCE" --source nse
uv run openfilings search "Alicorp" --source smv
uv run openfilings search "Ecopetrol" --source sfc
```

BMV annual PDFs and quarterly IFRS JSON archives both convert to Markdown and
normalized financial statements. Peru reads SMV statement tables through
bounded official statement operations with limited request concurrency.

Canada supports official TSX/TSXV listed-company discovery plus explicit
user-selected filing imports. Search and cache the issuer first. Then use the
SEDAR+ document search's **Generate URL** action:

```bash
uv run openfilings search "SHOP" --source sedar
uv run openfilings import-sedar ca_sedar_tsx_SHOP \
  "https://www.sedarplus.ca/csa-party/..." \
  --title "2025 Annual Report" \
  --filing-date 2026-03-12 \
  --period-end 2025-12-31
uv run openfilings filings ca_sedar_tsx_SHOP --source sedar
uv run openfilings fetch ca_sedar_filing_RETURNED_ID -o shopify-2025.md
```

If SEDAR+ returns a browser-verification page for the generated URL, download
the PDF normally and import the local file instead:

```bash
uv run openfilings import-sedar ca_sedar_tsx_SHOP shopify-2025.pdf \
  --source-url "https://www.sedarplus.ca/csa-party/..." \
  --title "2025 Annual Report" \
  --filing-date 2026-03-12 \
  --period-end 2025-12-31
```

URLs are restricted to official HTTPS SEDAR+ paths and redirects cannot escape
the allowlist. Imports accept PDFs up to 100 MB. The compressed original shares
the configured cache budget with Markdown and structured financials.

Use the real filing ID returned by `filings` in the last two commands. Available
source values are `all`, `fca-nsm`, `edinet`, `esef`, `cvm`, `sgx`, `bmv`, `nse`,
`sedar`, `smv`, and `sfc`. Use
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
from openfilings import OpenFilings

async with OpenFilings.from_settings() as openfilings:
    company = await openfilings.company("Nokia", source="esef")
    filings = await company.get_filings(source="esef", limit=100)
    filing = filings.latest()
    assert filing is not None

    markdown = await filing.markdown()
    document = await filing.obj()
    matches = await filing.search("revenue operating profit")

    financials = await filing.financials()
    income = financials.income_statement()
    if income is not None:
        print(income.to_markdown())

    # Cache processed documents and financials for offline reuse.
    result = await filings.head(5).prefetch(documents=True, financials=True)
    print(result)

    # Later, browse previously cached metadata without regulator requests.
    cached_company = await openfilings.company("Nokia", offline=True)
    cached_filings = await cached_company.get_filings(offline=True)
```

Company search results and filing collections support slicing, `head`, `find`,
`filter`, and `latest`. Bound filings expose `markdown`, `obj`, `sections`,
ranked `search`, `financials`, and `xbrl` methods. Prefetching retains compressed
processed results while continuing to discard source documents. Explicit
SEDAR+ imports are the exception: their compressed source PDF is retained so
future extraction does not depend on the browser session.

Canadian imports are also available through the service API:

```python
from datetime import date

async with OpenFilings.from_settings() as openfilings:
    filing = await openfilings.import_sedar_filing(
        "ca_sedar_tsx_SHOP",
        document_url="https://www.sedarplus.ca/csa-party/...",
        title="2025 Annual Report",
        filing_date=date(2026, 3, 12),
        period_end=date(2025, 12, 31),
    )
    print(await filing.markdown())
```

Financial statements support `to_records()`, `to_markdown()`, and optional
pandas conversion. Install the extra only when DataFrames are needed:

```bash
uv sync --extra dataframe
```

Structured values preserve their source concept, period, unit, decimals, and
dimensions. The lower-level `OpenFilingsService`, normalized models, and raw
`list_filings` methods remain available for integrations that need them.

## MCP tools

- `companies_search(query, limit=5, source="all")`
- `filings_list(company_id, category="accounts", limit=10, source="all", history_days=120)`
- `sedar_filing_import(company_id, document_url, title, filing_date, period_end=None, filing_type="annual", category="accounts")`
- `filing_outline(filing_id, limit=100, refresh=False)`
- `filing_read(filing_id, section, offset=0, max_chars=6000, refresh=False)`
- `filing_search(filing_id, query, limit=5, snippet_chars=1200)`
- `filing_financials(filing_id, statements=None, periods=4, detail="standard", max_line_items=40)`
- `filing_markdown(filing_id, offset=0, max_chars=12000, refresh=False, ocr_mode=None)`

The MCP interface uses progressive disclosure. Start with company and filing
metadata, inspect a filing's outline, then read one section or retrieve short
ranked excerpts. Full Markdown is paginated and capped at 24,000 characters per
call. Financial responses can be restricted by statement, period, detail level,
and line-item count. Every response includes a compact success envelope and
suggested next steps where another focused call is useful. `filing_sections`
remains as a compatibility alias that returns headings without section bodies.

Start the stdio server with `uv run openfilings serve`.

## Production checks

Pull requests and main-branch changes run locked dependency installation, Ruff,
the complete offline suite, Python 3.11–3.14 compatibility, wheel and source
distribution builds, and installed-package smoke tests. CodeQL and a strict
locked-dependency audit run separately. A scheduled keyless smoke job checks
one listed issuer and current filing for FCA, ESEF, CVM, SGX, BMV, NSE, SMV,
and SFC. Japan is excluded because its filing API requires a key; Canada is
excluded because SEDAR+ permits browser search but not stable automated
retrieval.

Run the same local gates before a release:

```bash
uv sync --locked --all-extras --dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
uv build
uv run openfilings-smoke
```

See `CONTRIBUTING.md` for the release and rollback checklist and `SECURITY.md`
for vulnerability reporting and supported-version policy.

## Resource footprint

The complete development environment is approximately 129 MB. Normal search
and listing operations use only HTTP plus SQLite. PDF extraction loads native
PDF libraries only when required; HTML extraction is lightweight and local.
Source documents are discarded after conversion, while Markdown and normalized
financials are compressed. Inline XBRL is processed with a bounded streaming
parser, avoiding a full DOM for large annual reports. Tesseract is an optional
system executable and adds nothing to the Python environment when it is not
installed.

EDINET issuer search downloads a roughly 0.6 MB compressed code list. Filing
ZIPs are capped at 150 MB and discarded after conversion; the default Japanese
history window makes 120 small metadata requests and is reused for six hours.
European ESEF retrieval downloads the main XHTML report directly instead of the
complete report package, reducing bandwidth and temporary memory use.
Brazilian search downloads the roughly 1.4 MB CVM register once per process.
Filing history reads up to five annual IPE ZIP indexes (currently about 1–2 MB
each), retaining only normalized matching records; report PDFs remain bounded
by the same 150 MB document limit and are discarded after conversion.
Singapore search downloads SGX's roughly 0.13 MB stock list and 8.3 MB metadata
feed once per process, then retains only normalized Mainboard and Catalist
companies. Annual-report history uses one small paged query; validated PDFs use
the shared 150 MB limit and are discarded after conversion.
The new adapters request bounded official issuer and filing feeds on demand.
Mexico, India, China, and Colombia download only selected filing PDFs or ZIPs;
Peru renders normalized HTML statement tables directly from SMV's open datasets.
Canada queries the small TSX/TSXV directory endpoints and does not automate
SEDAR+ discovery. Only a user-selected SEDAR+ URL or local PDF is downloaded,
validated, compressed, and retained within the configured cache budget.

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

The Japan connector uses the official [EDINET API v2
specification](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf)
and [issuer-code
archive](https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip).
It sends a subscription key only as an API parameter and never persists it.

The Netherlands, France, Spain, Italy, Denmark, Sweden, and Finland connectors
use the free, keyless
[filings.xbrl.org API](https://filings.xbrl.org/docs/api). XBRL International
sources ESEF reports from the relevant national collection authority; its index
can lag or omit filings, so this feed should not be treated as a real-time legal
record. Germany is not enabled because the upstream repository currently lists
German filings as unavailable for reliable discovery and download.

The Brazil connector uses the official keyless CVM
[listed-company register](https://dados.cvm.gov.br/dataset/cia_aberta-cad) and
[IPE filing archive](https://dados.cvm.gov.br/dataset/cia_aberta-doc-ipe). It
filters the register to active operational issuers whose market type is
`BOLSA`; document links remain on CVM's public RAD system.

The Singapore connector uses SGX's official keyless
[corporate-information page](https://www.sgx.com/securities/corporate-information),
[listed-stock API](https://api.sgx.com/securities/v1.1/stocks), and
[financial-reports API](https://api.sgx.com/financialreports/v1.0). It joins
stock counters to SGX issuer metadata, keeps Mainboard and Catalist companies,
and validates both announcement-detail and PDF-attachment paths.

Mexico uses BMV's official issuer and financial-information services. India
uses NSE's listed-equity CSV and annual-report service for filing discovery;
financial statements are read directly from NSE's Integrated Filing XBRL
(the exclusive format for SEBI Regulation 33 financial results since April
2025 - PDF submission was discontinued), falling back to the annual-report
PDF only if no audited XBRL filing covers that exact fiscal year-end. Peru
uses SMV's open financial-statement datasets, and Colombia uses SFC/SIMEV's
current BVC-equity and financial-report services. For Colombia, the balance
sheet is read directly from SFC's CUIF supervisory dataset on datos.gov.co
(assets, liabilities, and equity accounts reconcile exactly) instead of
parsed from the PDF filing; the income statement still comes from the PDF,
since CUIF reports income/expense accounts unclosed for supervisory
purposes. Each adapter filters out funds and other non-operating exchange
products and validates document hosts before download.

Canada uses the official TSX/TSXV company directory for issuer discovery.
SEDAR+ document search is public in a normal browser, but its stateful callbacks
and Radware anti-automation controls do not provide a stable public API
contract. OpenFilings therefore does not automate discovery or bypass those
controls. It accepts the platform's user-generated public document URLs and
browser-downloaded PDFs, then routes them through the normal filing pipeline
without a browser runtime.

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
