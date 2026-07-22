# OpenFilings

OpenFilings is a lightweight, local-first tool for searching listed UK,
Japanese, Brazilian, Taiwanese, Hong Kong, Singapore, Netherlands, French,
Spanish, Italian, Danish, Swedish, and Finnish companies, listing public
filings, and converting source documents into Markdown for LLMs.

## What works

- Search UK-listed issuers through the FCA National Storage Mechanism (NSM)
- Search Japanese filers by name, ticker, or EDINET code
- Search Netherlands, French, Spanish, Italian, Danish, Swedish, and Finnish
  ESEF issuers without a key
- Search active Brazilian exchange-listed issuers from the official CVM register
- List and download CVM annual and interim financial statements without a key
- Search the official TWSE listed-company universe by code or Chinese/English name
- List and download Chinese and English annual reports from TWSE/MOPS without a key
- Search current HKEX Main Board and GEM issuers while excluding funds and warrants
- List and download HKEXnews annual and interim reports without a key
- Search current SGX Mainboard and Catalist companies while excluding non-stock products
- List and download SGX annual reports without a key
- List and download keyless Inline XBRL financial reports from filings.xbrl.org
- List EDINET annual, semiannual, quarterly, and current reports
- Resolve listed-company names to legal entity identifiers (LEIs)
- List regulated disclosures in one timeline
- Download public PDF, HTML/XHTML, and tagged-report ZIP documents
- Prefer tagged XHTML annual reports over PDF when available
- Convert documents locally to Markdown without retaining the originals
- Navigate extracted documents by heading and search within sections
- Extract standardized income, balance-sheet, cash-flow, and comprehensive
  income statements from UK-GAAP, ESEF/IFRS, and EDINET Inline XBRL
- Derive high-confidence normalized statements from aligned CVM, TWSE, HKEX,
  and SGX PDF tables while preserving labels, periods, currencies, and scale
- Score extraction quality with explainable warnings
- Optionally route scanned PDFs through page-at-a-time Tesseract OCR
- Reuse compressed Markdown and duplicate content through a SQLite cache
- Enforce a logical cache limit and reclaim space with a cleanup command
- Use the same operations from the CLI or an MCP server

All seven filing-feed families are free. FCA, European ESEF, Brazilian CVM,
Taiwan TWSE/MOPS, Hong Kong HKEXnews, and Singapore SGX access need no key, and
EDINET company search is also keyless. EDINET filing retrieval requires free
API-key registration.

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
Only active `BOLSA` issuers are returned. CVM reports are PDFs; Markdown and
high-confidence normalized statement rows are available without a key:

```bash
uv run openfilings search "Banco do Brasil" --source cvm
uv run openfilings filings br_cvm_001023 --source cvm
uv run openfilings fetch br_cvm_1046308 -o banco-do-brasil-report.md
uv run openfilings financials br_cvm_1046308 -o banco-do-brasil-financials.json
```

Taiwan uses TWSE's keyless listed-company OpenAPI and MOPS document server.
Common Chinese names, legal names, English abbreviations, and stock codes work:

```bash
uv run openfilings search "台泥" --source twse
uv run openfilings filings tw_twse_1101 --source twse
uv run openfilings fetch tw_mops_2025_1101_20260522FE4 -o tcc-report.md
uv run openfilings financials tw_mops_2025_1101_20260522FE4 -o tcc-financials.json
```

Some TWSE English reports are image-only. With Tesseract installed, structured
extraction automatically uses bounded page-at-a-time OCR. Without Tesseract,
OpenFilings reports the limitation instead of returning empty tables.

Hong Kong uses HKEX's current securities list and public HKEXnews title search.
Only Main Board and GEM issuer equities are searchable; duplicate RMB counters,
ETFs, warrants, investment companies, and other non-company products are
excluded:

```bash
uv run openfilings search "HKEX" --source hkex
uv run openfilings filings hk_hkex_00388 --source hkex
uv run openfilings fetch hk_hkex_12052683 -o hkex-report.md
uv run openfilings financials hk_hkex_12052683 -o hkex-financials.json
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

Use the real filing ID returned by `filings` in the last two commands. Available
source values are `all`, `fca-nsm`, `edinet`, `esef`, `cvm`, `twse`, `hkex`,
and `sgx`. Use `--output report.md` with `fetch` to save Markdown. Set
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
processed results while continuing to discard source documents.

Financial statements support `to_records()`, `to_markdown()`, and optional
pandas conversion. Install the extra only when DataFrames are needed:

```bash
uv sync --extra dataframe
```

Structured values preserve their source concept, period, unit, decimals, and
dimensions. The lower-level `OpenFilingsService`, normalized models, and raw
`list_filings` methods remain available for integrations that need them.

## MCP tools

- `companies_search(query, limit=10, source="all")`
- `filings_list(company_id, category="accounts", limit=25, source="all", history_days=120)`
- `filing_markdown(filing_id, refresh=False, ocr_mode=None)`
- `filing_sections(filing_id, query=None, limit=20)`
- `filing_financials(filing_id, refresh=False)`

Start the stdio server with `uv run openfilings serve`.

## Production checks

Pull requests and main-branch changes run locked dependency installation, Ruff,
the complete offline suite, Python 3.11–3.14 compatibility, wheel and source
distribution builds, and installed-package smoke tests. CodeQL and a strict
locked-dependency audit run separately. A scheduled keyless smoke job checks
one listed issuer and current filing for FCA, ESEF, CVM, TWSE, HKEX, and SGX;
Japan is intentionally excluded because its filing API requires a key.

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
Taiwan search downloads TWSE's listed-company JSON once per process. Annual
history makes at most five small MOPS metadata requests by default and follows
TWSE's validated one-time PDF handoff. The PDFs use the shared 150 MB limit.
Hong Kong search downloads HKEX's roughly 1.3 MB securities workbook once per
process and parses its XLSX/XML with the Python standard library. Filing history
uses two small bounded HKEXnews queries for annual and interim reports; PDFs use
the shared 150 MB limit.
Singapore search downloads SGX's roughly 0.13 MB stock list and 8.3 MB metadata
feed once per process, then retains only normalized Mainboard and Catalist
companies. Annual-report history uses one small paged query; validated PDFs use
the shared 150 MB limit and are discarded after conversion.

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

The Taiwan connector uses the official keyless TWSE
[listed-company OpenAPI](https://openapi.twse.com.tw/) and the public
[MOPS/TWSE document server](https://doc.twse.com.tw/server-java/t57sb01). It
accepts only companies in TWSE's current listed-company dataset and validates
both the stable document request and TWSE's generated PDF handoff before
downloading.

The Hong Kong connector uses HKEX's official
[Full List of Securities](https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx)
and public
[HKEXnews Title Search](https://www1.hkexnews.hk/search/titlesearch.xhtml).
It keeps only Main Board and GEM issuer equities, deduplicates alternate currency
counters by ISIN, and validates every HKEXnews PDF path before downloading.

The Singapore connector uses SGX's official keyless
[corporate-information page](https://www.sgx.com/securities/corporate-information),
[listed-stock API](https://api.sgx.com/securities/v1.1/stocks), and
[financial-reports API](https://api.sgx.com/financialreports/v1.0). It joins
stock counters to SGX issuer metadata, keeps Mainboard and Catalist companies,
and validates both announcement-detail and PDF-attachment paths.

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
