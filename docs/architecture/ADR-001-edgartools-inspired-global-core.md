# ADR-001: EdgarTools-inspired global core

## Status

Accepted

## Context

OpenFilings targets non-US regulators. EdgarTools has a strong collection-first
API, document model, statement abstractions, caching patterns, and extensive
tests, but its central company, filing, attachment, form, and XBRL workflows are
coupled to SEC EDGAR, CIKs, accession numbers, SGML, and US-GAAP.

The existing OpenFilings implementation already has working Companies House and
FCA NSM adapters, normalized IDs, bounded extraction, OCR routing, compressed
storage, CLI/MCP interfaces, and live UK verification.

## Decision

Preserve OpenFilings' regulator-neutral adapters and normalized models while
adopting EdgarTools' collection and document ergonomics:

- immutable `Filings` collections with `filter`, `latest`, and `head`;
- navigable `FilingDocument` sections and local search;
- typed statements, line items, values, periods, dimensions, and provenance;
- fixture-driven parsing and cache-backed service methods.

Use a lightweight bounded streaming parser for UK/ESEF Inline XBRL extraction.
Keep Arelle outside the default runtime for standards-complete taxonomy loading
and conformance validation.

## Trade-offs

- The base installation avoids EdgarTools' SEC, Pandas, and PyArrow dependency
  graph, preserving the existing lightweight footprint.
- OpenFilings owns non-US taxonomy mappings and must maintain them as FRC/IFRS
  taxonomies evolve.
- The streaming fast path extracts application statements but is not presented
  as a validating XBRL processor; Arelle remains the validation path.

## Consequences

UK work remains intact, future market adapters share one stable public API, and
large ESEF reports can be processed without a full in-memory HTML tree. The
project acknowledges EdgarTools as an architectural reference in
`THIRD_PARTY_NOTICES.md`.

## Revisit trigger

Reconsider a shared runtime dependency if EdgarTools publishes a stable,
regulator-neutral document/XBRL package that does not require SEC-specific
models or the full analytical dependency graph.
