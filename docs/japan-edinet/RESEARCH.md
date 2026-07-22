# Japan EDINET Research

## Overview

Add Japan as the second OpenFilings market using the Financial Services
Agency's EDINET API and issuer-code directory.

## Problem Statement

The global plan describes an obsolete EDINET v1 search API and no
authentication. The current official interface is EDINET API v2. It exposes
date-based document lists and document downloads, and requires a subscription
key. EDINET separately publishes a fixed-link issuer-code ZIP that can support
company search without an API key.

## User Stories / Use Cases

- Search Japanese filers by Japanese/English name, EDINET code, or ticker.
- List at least 120 days of annual, semiannual, quarterly, and current reports.
- Convert a filing's multi-file Inline XBRL package to Japanese Markdown.
- Extract normalized JP-GAAP/IFRS statements with source-fact provenance.
- Use the same operations from Python, CLI, and MCP.

## Technical Research

### Approach Options

- Scraping the EDINET search site would avoid an API key but couples the app to
  a stateful GeneXus UI.
- Scanning daily API lists can discover issuers but makes company search slow.
- The official `Edinetcode.zip` fixed link is small, public, and purpose-built
  for filer identity lookup.

### Recommended Approach

Use `Edinetcode.zip` for company search. Use EDINET API v2 `documents.json` for
date-based filing history and `/documents/{docID}` with `type=1` for the
official ZIP containing Inline XBRL and supporting HTML. Apply bounded retries,
a client-side request interval, archive limits, and a 120-day default history.

EDINET reports can split one filing over several `XBRL/PublicDoc` HTML files.
Combine those documents in filename order for Markdown and feed their Inline
XBRL tags into the existing streaming parser. Add JP-GAAP aliases to the shared
line-item map instead of adding a regulator-specific financial model.

### Required Technologies

- Existing Python, httpx, SQLite, Markdownify, and streaming iXBRL stack
- `EDINET_API_KEY` for filing lists and downloads
- No additional runtime dependencies

### Data Requirements

Persist normalized Japanese companies and filings in the existing SQLite
tables. Keep API keys out of payloads, URLs, logs, and Markdown. Discard source
archives after extraction.

## UI/UX Considerations

Add `edinet` as a source and explain that search works without a key while
filing retrieval requires one. Preserve Japanese text and expose English
standardized financial line-item codes.

## Integration Points

- EDINET adapter and environment settings
- Shared company/filing models
- Service routing, CLI source option, and MCP tools
- ZIP/HTML extraction and XBRL line-item mapping
- Existing compressed content and financial caches

## Risks and Challenges

- EDINET returns HTTP 200 with JSON for some API errors; content type must be
  checked before treating downloads as ZIP/PDF.
- Daily list calls can trigger 429 responses; requests must be paced and retried.
- Issuer extensions and industry-specific taxonomies require alias growth based
  on representative live reports.
- A filing can contain many HTML files and linked assets; archive counts and
  expanded size must remain bounded.

## Open Questions

- Broader industry taxonomy coverage should follow live issuer measurements.

## References

- [EDINET API v2 specification](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf)
- [EDINET operation guides](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WEEK0060.html)
- [Official EDINET issuer-code archive](https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip)
- [EDINET viewer](https://disclosure2.edinet-fsa.go.jp/)
