# UK Filing Markdown Progress

## Status: UK scope complete through Phase 4

## Quick Reference

- Research: `docs/uk-filing-markdown/RESEARCH.md`
- Implementation: `docs/uk-filing-markdown/IMPLEMENTATION.md`

## Phase Progress

### Phase 1: UK Filing-to-Markdown Vertical Slice

**Status:** Completed

#### Tasks Completed

- Defined the phase boundaries and architecture.
- Added normalized company, filing, and filing-content models.
- Added Companies House search, paginated filing history, detail, and PDF retrieval.
- Added in-memory PDF-to-Markdown conversion without retaining source PDFs.
- Added compressed SQLite metadata and Markdown caching.
- Added CLI commands and three matching MCP tools.
- Added eight offline tests covering adapters, extraction, service orchestration,
  error handling, and cache reuse.
- Passed Ruff, Python compilation, CLI startup, MCP import, and all tests.

#### Decisions Made

- Use Companies House as the first official source.
- Use PyMuPDF4LLM without OCR for the default extraction path.
- Compress Markdown in SQLite and discard downloaded PDF bytes after processing.
- Pin MCP to stable v1 until v2 reaches a stable release.

#### Blockers

- None for implementation. A live smoke test remains pending until a Companies
  House API key is configured locally.

### Phase 2: FCA NSM Listed-Company Coverage

**Status:** Completed

#### Tasks Completed

- Confirmed the FCA site uses a free public read-only search endpoint.
- Confirmed the current index alias is `nsm-search` and validated company/LEI
  filtering against live responses.
- Confirmed disclosure metadata contains LEIs, related issuers, categories,
  correction flags, and direct public document paths.
- Confirmed live document formats include HTML and PDF, with tagged annual
  reports also exposed as structured packages.
- Added a dedicated read-only FCA NSM adapter with bounded retries and safe
  artefact URL validation.
- Added LEI-aware issuer models and Companies House/FCA identity merging.
- Added combined filing timelines with explicit source provenance.
- Added local HTML/XHTML and tagged ZIP-to-Markdown extraction.
- Added content-hash duplicate reuse and conservative metadata deduplication.
- Added source selection to all relevant CLI commands and MCP tools.
- Added seven Phase 2 tests, bringing the offline suite to 15 passing tests.
- Passed a live keyless FCA smoke test for issuer search, filing listing, and
  HTML-to-Markdown retrieval.

#### Risk

- The FCA search endpoint is used by its public web application but is not a
  separately versioned consumer API. The adapter isolates the index alias and
  payload schema so changes remain local.

### Phase 3: Extraction Quality and Operations

**Status:** Completed

#### Tasks Completed

- Researched PyMuPDF and Tesseract OCR routing guidance.
- Confirmed Tesseract is not installed on the current machine, so OCR must
  remain optional and testable without changing the base footprint.
- Added deterministic quality scoring with character density, alphanumeric,
  encoding-noise, and line-structure signals.
- Added `auto`, `never`, and `always` OCR routing modes.
- Added page-at-a-time RGB rendering and streamed Tesseract invocation with
  page limits and per-page timeouts.
- Persisted extraction quality in SQLite with an automatic legacy migration.
- Added a 512 MB default logical cache limit plus `cache status` and
  `cache prune` commands.
- Added `inspect-document` for local quality reports and elapsed-time benchmarks.
- Added a recorded FCA NSM response fixture and synthetic scanned-PDF tests.
- Expanded the offline suite from 15 to 24 passing tests.

#### Decisions Made

- Score every extraction with explainable content-quality signals.
- Keep `auto` as the default: use OCR only for unusable PDFs and only when a
  Tesseract executable is available.
- Stream one RGB page image at a time to Tesseract and enforce page/time limits.
- Persist quality metadata beside cached Markdown and surface it through CLI/MCP.

### Phase 4: UK Structured Financials and Domain API

**Status:** Completed

#### Tasks Completed

- Audited EdgarTools' document, filing-collection, and XBRL architecture.
- Confirmed the reusable patterns and isolated SEC-specific dependencies.
- Located and downloaded a representative public Tesco ESEF report package.
- Added immutable filing collections and section-aware filing documents.
- Added Companies House content negotiation for XHTML and ZIP tagged accounts.
- Added a bounded streaming iXBRL parser with contexts, units, dimensions,
  transformations, signs, scales, and precision metadata.
- Normalized UK-GAAP and IFRS facts into four statement types while preserving
  source-concept provenance.
- Added compressed structured-financial caching plus Python, CLI, and MCP APIs.
- Expanded the offline suite from 24 to 31 passing tests.
- Verified the complete workflow against Tesco's 2026 public FCA ESEF package:
  718 usable numeric facts across four normalized statements.
- Benchmarked its 29.5 MB Inline XBRL report at roughly 0.63 seconds and 150 MB
  peak resident memory; the full development environment remains about 129 MB.

#### Decisions Made

- Preserve the working UK adapters, cache, quality routing, CLI, and MCP.
- Adopt EdgarTools-style collection/document ergonomics without depending on its
  Pandas/PyArrow/SEC runtime.
- Use a bounded streaming iXBRL fast path for extraction and keep Arelle as the
  optional standards-complete validation path.

#### Blockers

- None.

## Session Log

### 2026-07-22

- Completed Phase 1 implementation.
- Verification: `8 passed`; Ruff reported no violations.
- The complete development environment occupies approximately 127 MB.
- Started Phase 2 and validated the live FCA NSM search/document interfaces.
- Completed Phase 2 implementation.
- Verification: `15 passed`; Ruff, formatting, compilation, CLI startup, and
  MCP import all passed.
- Live FCA verification resolved Tesco's LEI, listed current disclosures, and
  converted a public announcement into a 5 KB Markdown document.
- Completed Phase 3 quality routing and operational hardening.
- Verification: `24 passed`; Ruff reported no violations.
- Verified the additive database migration against the existing live FCA cache.
- Refetched a Tesco disclosure at `100/100` extraction quality and confirmed
  the full development environment remains approximately 129 MB.
- Completed Phase 4 structured financials and EdgarTools-inspired domain APIs.
- Verification: `31 passed`; Ruff, formatting, compilation, CLI startup, and
  MCP import all passed.
- Live FCA verification converted Tesco's 2026 annual report to 968 KB of
  quality-scored Markdown and extracted four structured statements.

## Files Changed

- `.env.example`, `.gitignore`, `pyproject.toml`, `README.md`
- `src/openfilings/` adapter, extraction, storage, service, CLI, and MCP modules
- `tests/` offline adapter, extraction, XBRL, service, and storage tests
- `tests/fixtures/` recorded FCA metadata fixture
- `docs/uk-filing-markdown/` build records
- `uv.lock`

## Architectural Decisions

- Keep source adapters separate from extraction and storage.
- Make external clients and converters injectable for deterministic tests.
- Treat LEI as the cross-source identity key while retaining Companies House IDs
  as the preferred ID for exact name matches.
- Isolate the unversioned FCA public search schema in one adapter.
- Default to conditional OCR so scanned documents improve without making every
  PDF slow or increasing the base Python footprint.
- Bound cache content logically during writes and reserve `VACUUM` for an
  explicit prune command to avoid expensive maintenance on every fetch.
- Reuse EdgarTools' collection-first design ideas, but keep source adapters and
  XBRL normalization regulator-neutral instead of inheriting SEC assumptions.
- Stream Inline XBRL in chunks and cap input, contexts, and facts to keep annual
  report parsing deterministic on an ordinary laptop.

## Lessons Learned

- Lazy-loading PDF libraries keeps ordinary CLI startup clean and lightweight.
- Injecting the source client and converter makes the full workflow testable
  without API credentials or network access.
- Preserving block-level HTML elements materially improves announcement
  readability; stripping `div` elements concatenates header lines.
- Quality metadata needs an additive SQLite migration because existing users may
  already have cached Phase 1/2 content.
