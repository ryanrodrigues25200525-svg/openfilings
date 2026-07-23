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

### Fixed

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
