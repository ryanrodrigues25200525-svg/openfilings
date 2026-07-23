# Changelog

All notable changes to OpenFilings are documented in this file.

## Unreleased

### Added

- Brazil now reads CVM's Open Data DFP/ITR datasets directly for financial
  statements - a standardized chart of accounts published as free bulk
  CSV/ZIP archives - instead of parsing the PDF filing. Falls back to the
  existing PDF-heuristic path when a company/year isn't in the dataset.
- Colombia's balance sheet is now read directly from SFC's CUIF supervisory
  dataset on datos.gov.co (assets/liabilities/equity accounts reconcile
  exactly across every regulated entity type) and merged with the PDF
  filing's other statements. The income statement still comes from the PDF,
  since CUIF reports income/expense accounts unclosed for supervisory
  purposes - even at year-end, revenue exactly equals expenses.
- India now reads NSE's "Integrated Filing - Financials" XBRL directly -
  the exclusive format for SEBI Regulation 33 financial results since April
  2025, when PDF submission was discontinued - instead of parsing the
  annual-report PDF. Reuses the existing tagged-XBRL statement pipeline
  entirely: the taxonomy's concept names already match the standard IFRS
  concepts recognized elsewhere. Falls back to the PDF annual report if no
  audited filing covers the exact fiscal year-end. Verified against
  Reliance Industries and Tata Consultancy Services.
- The `filing_financials` MCP tool now points the calling agent to
  `filing_search`/`filing_markdown` when structured extraction fails,
  instead of just a bare error - the filing's own converted text is still
  readable even when the heuristic statement parser can't make sense of it.
- The scheduled live smoke suite now fetches each checked filing's
  financials and verifies the balance-sheet identity holds, instead of
  only checking that a filing was found - this is the check that would
  have caught this session's Sweden and Singapore bugs automatically, on
  a schedule, instead of only when someone thinks to spot-check by hand.
  Coverage expanded to one issuer per ESEF jurisdiction (previously one
  for all of ESEF) plus company-search-only checks for Canada and Japan.

### Fixed

- FCA NSM has no generic "category" filter of its own, only disclosure type
  codes, so `category="accounts"` (the default) was a silent no-op -
  `filings()`/`get_filings()` could return the newest disclosure of any
  type (a director dealing, an admission notice) instead of a financial
  statement. Now maps to the "ACS" (accounts) type code automatically
  unless the caller passes their own `nsm_type_codes`.
- PDF-derived financials (aligned-text path): a page footer or a stray note
  reference carrying a real word alongside an embedded digit (e.g. a page
  number) is no longer stripped down to a false numeric value; the forward
  scan for a row's own numbers now stops after a run of unrelated prose
  instead of reading through it.
- PDF-derived financials: "Statement of Changes in Equity" is now detected
  as a statement boundary in more layouts (a missing "consolidated"-prefixed
  heading variant, and sentence-case headings beyond the first 12 lines),
  so its rows (equity components, not fiscal years) no longer overwrite a
  same-named row from a different statement.
- PDF-derived financials: a note reusing a grand-total label ("Total
  Assets") for a narrower scope (a subsidiary, a structured entity) no
  longer wins over the real consolidated total when both candidates tie on
  period count - the larger value is preferred, since a note's total can
  only be a subset of the entity's real total.
- Tagged-XBRL financials (any inline-XBRL market: UK-GAAP, ESEF, JP-GAAP,
  and now India): when a filer tags both a full total (e.g. "Equity") and a
  narrower component with identical period/context coverage (e.g.
  "EquityAttributableToOwnersOfParent", excluding non-controlling
  interests), the concept selection previously fell back to an arbitrary
  tie-break and could silently pick the narrower one, breaking the
  balance-sheet identity. The alias list's own order (the full total listed
  first) now breaks the tie.

- PDF-derived financials: a labeled row followed by an unlabeled sub-item
  breakdown (no repeated "Total X" line) no longer misattributes the first
  segment's value as the row's own total.
- PDF-derived financials: a combined subtotal label (e.g. "Passivo
  circulante e não circulante") no longer overwrites its own component's
  correct value.
- PDF-derived financials: Indian lakh/crore number grouping (e.g.
  "2,57,935") is now parsed correctly - previously failed silently on
  every NSE filing using this convention.
- PDF-derived financials: ratio-analysis disclosures, qualified variants
  (e.g. "X Under Development"), and grand totals restating a different
  total no longer falsely match unrelated line items.
- PDF-derived financials: a single-word alias (e.g. "revenue", "goodwill")
  no longer matches prose that merely starts with that word (e.g. "Revenue
  Reserves", "Goodwill is reviewed on an annual basis...").
- PDF-derived financials: a statement heading wrapped across separate
  extracted lines is now detected via a joined window instead of missed.
- Added missing common English aliases ("Net Revenue", "Income from
  Operations", "Net Income", "Total Current/Non-Current Assets/
  Liabilities") and a PDF ligature-extraction artifact ("Gross Proft" for
  "Gross Profit").

### Removed

- Removed Taiwan (TWSE/MOPS), Hong Kong (HKEX/HKEXnews), and mainland China
  (SSE/CNINFO) support.
- Removed a dead, unreferenced `_is_nsm_company_id` helper.

### Documentation

- Added a complete system architecture document covering components, data
  flows, adapter contracts, persistence, resource boundaries, trade-offs, and
  market-extension guidance.
- Added a project description defining the product scope, supported markets,
  capabilities, technology, maturity, and current limitations.

## 0.21.0 - 2026-07-23

- Added durable Canadian filing imports from allowlisted SEDAR+ generated URLs
  or browser-downloaded local PDFs.
- Routed imported filings through the existing CLI, Python, Markdown, section,
  structured-financial, and token-bounded MCP interfaces.
- Added compressed source-document storage, cache-budget accounting, redirect
  confinement, PDF validation, and explicit browser-verification recovery.

## 0.20.0 - 2026-07-23

- Added keyless listed-company and filing adapters for Mexico BMV, India NSE,
  mainland China CNINFO/SSE, Peru SMV, and Colombia SFC/SIMEV.
- Added official TSX/TSXV Canadian company discovery with an explicit SEDAR+
  browser-only filing limitation.
- Added retry-bounded shared HTTP handling, strict document-host validation,
  live smoke coverage, and Spanish and Simplified Chinese PDF table aliases.
- Added INR, MXN, CAD, CNY, PEN, and COP structured-statement currency support.
- Redesigned MCP responses for LLM use with compact metadata, progressive
  disclosure, section outlines, targeted reads, ranked excerpts, pagination,
  structured recovery guidance, and bounded financial-statement filters.
- Added native Markdown and structured-statement extraction for BMV quarterly
  IFRS JSON archives.
- Replaced Peru's 100,000-row SMV downloads with bounded official statement
  operations and limited request concurrency.

## 0.19.0 - 2026-07-23

- Added keyless listed-company adapters for seven ESEF markets, Brazil, Taiwan,
  Hong Kong, and Singapore while retaining the existing Japan connector.
- Added EdgarTools-style company, filing-collection, document-section, search,
  prefetch, and financial-statement APIs.
- Added high-confidence structured extraction from native and OCR-derived PDF
  statement text for CVM, TWSE, HKEX, and SGX reports.
- Removed Companies House so UK discovery contains exchange-listed issuers from
  the FCA path only.
- Added locked CI, Python compatibility, package smoke tests, CodeQL, dependency
  auditing, dependency updates, and scheduled keyless regulator smoke tests.
