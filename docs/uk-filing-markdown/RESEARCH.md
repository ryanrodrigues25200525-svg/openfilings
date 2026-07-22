# UK Filing Markdown Research

## Overview

Build a UK workflow that turns official FCA National Storage Mechanism (NSM)
filings for publicly listed issuers into Markdown usable by a person or MCP
client.

## Problem Statement

Financial filings are available from official sources but arrive as metadata
and print-oriented documents. The first useful product should retrieve and
normalize those documents without prematurely building cross-market financial
statement standardization.

## User Stories / Use Cases

- Search a UK company by name.
- Resolve a listed issuer to its LEI.
- List FCA regulated disclosures.
- Fetch one filing as Markdown.
- Call the same operations from an LLM through MCP.

## Technical Research

### Approach Options

- PyMuPDF4LLM provides local PDF-to-Markdown conversion without a GPU.
- SQLite keeps the first release self-contained and supports compressed content.
- The stable v1 MCP Python SDK supports a small stdio FastMCP server.
- The FCA web application uses a public read-only JSON search endpoint at
  `https://api.data.fca.org.uk/search?index=nsm-search`. Its current request
  shape can filter `company_lei`, `latest_flag`, dates, source, ESEF status,
  and headline codes.
- NSM search responses include disclosure IDs, company names, LEIs, related
  issuers, dates, categories, and relative document links under
  `https://data.fca.org.uk/artefacts/`.
- FCA source documents currently include PDF, HTML, XHTML, and ZIP-packaged
  tagged annual financial reports, so extraction must dispatch by media type.
- EdgarTools' collection and document ergonomics are reusable, but its company,
  attachment, and financial-statement pipeline is coupled to SEC forms, SGML,
  FilingSummary, and US-GAAP conventions.
- Arelle supports XBRL and Inline XBRL taxonomy loading and validation. It is a
  suitable optional conformance path, but too large for the default local-first
  extraction footprint.

### Recommended Approach

Use thin source adapters, a service layer for orchestration and identity
resolution, and separate storage and extraction modules. Keep the FCA index and
request schema isolated in one adapter because the public endpoint is used by
the FCA website but is not published as a versioned consumer API.

Adopt EdgarTools' immutable collection and parsed-document interfaces while
implementing regulator-neutral models locally. Extract common UK-GAAP and IFRS
facts with a bounded streaming Inline XBRL parser, retain every value's source
concept and context, and leave full taxonomy/conformance validation to optional
Arelle workflows.

For difficult PDFs, score the fast extraction before invoking OCR. Use a small
set of explainable signals: extracted character count, characters per page,
alphanumeric ratio, replacement-character ratio, and line structure. Only
route unusable PDFs to OCR in `auto` mode.

Use the system Tesseract executable as an optional subprocess. Render one page
at a time with PyMuPDF in RGB without transparency, stream PNG bytes to
Tesseract, and discard them immediately. This avoids another Python dependency,
keeps peak memory bounded to one rendered page, and leaves the normal install
unchanged. `always`, `auto`, and `never` modes make the latency trade-off
explicit.

### Required Technologies

- Python 3.11+
- httpx and Pydantic
- PyMuPDF4LLM
- Markdownify for HTML/XHTML documents
- Optional Tesseract 5 executable for scanned PDFs
- SQLite from the standard library
- Typer and MCP Python SDK v1

### Data Requirements

Store normalized companies, LEIs, filings, document provenance, content hashes,
and compressed Markdown. Do not retain source documents after extraction.

## UI/UX Considerations

The CLI should expose short stable IDs and readable tables. MCP tools should
return the same normalized schemas rather than separate representations.

## Integration Points

FCA NSM search and downloads are public and free; the default client uses
conservative page sizes, bounded retries, and no background crawling.

## Risks and Challenges

- Some filing records have no downloadable document.
- The NSM endpoint is public but not a documented, versioned consumer API, so
  its index name and schema may change.
- Document content types vary and HTML tables are often generated from Word.
- PDF extraction may preserve headers or imperfect tables.
- OCR is much slower than native text extraction and loses typography and some
  table structure; page limits and timeouts are required.
- Tesseract language packs must be installed outside the Python environment.
- PyMuPDF licensing must be considered before proprietary distribution.

## Open Questions

- Additional taxonomy aliases should be driven by measured issuer coverage.
- Packaging and release automation remain separate from the completed UK core.

## References

- [FCA National Storage Mechanism](https://data.fca.org.uk/#/nsm/nationalstoragemechanism)
- [FCA ESEF preparation guidance](https://www.fca.org.uk/publication/fca/guidelines-preparation-esef-annual-financial-reports-fca.pdf)
- [HMRC XBRL guide for UK businesses](https://www.gov.uk/government/publications/xbrl-guide-for-uk-businesses/xbrl-guide-for-uk-businesses)
- [EdgarTools](https://github.com/dgunning/edgartools)
- [Arelle command line](https://arelle.readthedocs.io/en/latest/command_line.html)
- [PyMuPDF4LLM](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/)
- [Markdownify](https://github.com/matthewwithanm/python-markdownify)
- [Tesseract](https://tesseract-ocr.github.io/)
- [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk)
