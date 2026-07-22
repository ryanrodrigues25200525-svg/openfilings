# Japan EDINET Progress

## Status: Implemented

### 2026-07-22

- Confirmed EDINET API v2 is the current interface.
- Corrected the global plan's obsolete no-key and search-endpoint assumptions.
- Verified the public issuer-code fixed link and its CP932 CSV schema.
- Confirmed v2 date-list fields, document type codes, type-1 ZIP layout, API-key
  requirement, JSON error behavior, and 429 response guidance.
- Added keyless Japanese/English issuer search from the official CP932 archive.
- Added paced 120-day filing history, document downloads, and normalized types.
- Added multi-document EDINET Markdown and JP-GAAP financial extraction.
- Integrated EDINET with the service, CLI, MCP server, SQLite cache, and config.
- Passed Ruff, all 36 tests, bytecode compilation, and CLI/MCP startup checks.

## Decisions

- Use the official issuer-code archive for keyless company search.
- Use a 120-day default lookback for the three-month requirement.
- Reuse the regulator-neutral streaming Inline XBRL and statement models.
- Add no new runtime dependency.

## Live Certification

The official issuer archive was downloaded and parsed successfully. Recent
filing history and document download require a user-owned free EDINET API v2
subscription key; set `EDINET_API_KEY` before running the live examples.
