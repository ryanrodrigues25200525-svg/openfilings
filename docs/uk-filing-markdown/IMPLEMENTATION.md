# UK Filing Markdown Implementation Plan

## Overview

Implement a local-first UK vertical slice using Companies House, PDF-to-Markdown
conversion, SQLite caching, a CLI, and MCP.

## Prerequisites

- Python 3.11 or newer
- `uv`
- A Companies House API key for live requests

## Phase Summary

1. Build and verify the UK filing-to-Markdown vertical slice.
2. Add FCA NSM listed-company disclosures and identity resolution.
3. Add extraction quality routing and operational hardening.
4. Complete UK coverage with structured iXBRL financials and an ergonomic
   EdgarTools-inspired domain API.

---

## Phase 1: UK Filing-to-Markdown Vertical Slice

### Objective

Search companies, list filings, retrieve a document, convert it to Markdown,
cache it, and expose the workflow through CLI and MCP.

### Rationale

This is the smallest independently useful version and validates every core
architectural boundary.

### Tasks

- [x] Create package configuration and normalized models.
- [x] Implement Companies House search, filing, and document retrieval.
- [x] Implement PDF-to-Markdown conversion and compressed SQLite caching.
- [x] Add CLI and MCP interfaces.
- [x] Add offline tests and user documentation.

### Success Criteria

All tests pass offline; live commands require only a Companies House API key;
repeated Markdown retrieval is served from the local cache.

### Files Likely Affected

`src/openfilings/`, `tests/`, `README.md`, and `pyproject.toml`.

---

## Phase 2: FCA NSM Listed-Company Coverage

### Objective

Add annual reports, prospectuses, and regulated announcements for UK-listed
issuers, resolving Companies House IDs to LEIs and market identifiers.

### Tasks

- [x] Confirm a public, read-only FCA NSM retrieval mechanism.
- [x] Add issuer identity and LEI mapping.
- [x] Normalize NSM filings through the shared service.
- [x] Add HTML/XHTML/ZIP extraction alongside PDF extraction.
- [x] Add provenance and duplicate detection across sources.
- [x] Expose source selection through CLI and MCP.
- [x] Add offline adapter and end-to-end tests.

### Success Criteria

A listed issuer can be searched once, carries its LEI into the local identity
cache, and returns both Companies House and FCA NSM filings. Repeated or
byte-identical source documents reuse cached Markdown.

---

## Phase 3: Extraction Quality and Operations

### Objective

Route difficult documents through optional OCR/Marker processing and add cache
management, metrics, and robust live-source integration tests.

### Tasks

- [x] Add extraction quality checks.
- [x] Add optional Tesseract OCR fallback.
- [x] Add cache limits and cleanup commands.
- [x] Add recorded live-source fixtures and a local inspection benchmark.

### Success Criteria

Known difficult fixtures produce usable Markdown while the default installation
remains lightweight.

---

## Phase 4: UK Structured Financials and Domain API

### Objective

Prefer tagged Companies House accounts when available, extract standardized
statements from UK and ESEF iXBRL, and expose them through Python, CLI, and MCP.

### Tasks

- [x] Add EdgarTools-inspired filing collections and parsed document sections.
- [x] Negotiate Companies House XHTML/ZIP resources before falling back to PDF.
- [x] Add a bounded streaming iXBRL parser with contexts, units, dimensions, and
  numeric transformations.
- [x] Normalize UK-GAAP and IFRS facts into income statement, balance sheet, and
  cash-flow line items.
- [x] Cache structured financials and expose them through CLI and MCP.
- [x] Add synthetic and representative live ESEF verification.

### Success Criteria

UK issuers have search and historical filing access, documents render as quality
scored Markdown, and tagged annual reports return cached, provenance-preserving
structured financial statements without requiring the SEC-specific EdgarTools
runtime.

## Post-Implementation

- [x] Validate resource usage on representative annual reports.
- [ ] Add packaging and release automation.
