# Project Goal

**OpenFilings' defined scope is EdgarTools, but for non-US markets.**

EdgarTools is the reference implementation. Its code, architecture, and API
ergonomics are the pattern to follow, not just an inspiration to riff on:

- the collection-first API style (`Company` → `Filings` → `Filing`, with
  `.latest()`/`.filter()`/`.head()` helpers instead of raw list handling);
- normalized, immutable domain models returned from every source instead of
  regulator-specific shapes leaking through;
- structured financial statements as the default extraction target, with
  tagged/XBRL-style source data preferred over parsing prose whenever a
  market has it;
- a keyless, local-first library and CLI that works without an account or
  API key wherever the underlying regulator permits it;
- an MCP surface so an LLM agent can consume the same data ergonomically.

Where OpenFilings already mirrors this (see `src/openfilings/domain.py`'s
`Filings` collection, modeled explicitly on EdgarTools), keep doing so. Where
a new market or feature is being added, the default question is "how would
EdgarTools do this for SEC filings, and what's the equivalent regulator
contract for this market" - not "what's the fastest way to scrape this one
site."

## What "done" means

Full parity with EdgarTools' bar, applied market-by-market:

- comprehensive coverage of major non-US markets, not a handful of them;
- uniform structured-data quality within each covered market - tagged,
  reconciling financial statements, not heuristic PDF parsing as the norm;
- the same breadth of data types EdgarTools offers for the US (ownership,
  insider trading, institutional holdings, fund data, comparison tooling),
  not just filing discovery and financial-statement extraction;
- production-grade reliability validated continuously against the full
  universe of listed issuers per market, not spot-checked by hand.

## Where the project actually stands against that bar

(See `PROJECT_DESCRIPTION.md` for the current supported-markets table and
known constraints; this section is the honest gap assessment against the
goal above, not a restatement of what's shipped.)

- **Breadth**: 25 jurisdictions covered, not "all non-US markets." Missing
  major economies include China, Hong Kong (explicitly out of scope by
  decision), Switzerland, much of Scandinavia beyond the ESEF-covered
  jurisdictions, most of Latin America beyond Brazil/Mexico/Peru/Colombia,
  and virtually all of Africa and the Middle East.
- **Depth**: uneven within the 25. Brazil, India, the UK, and most of the EU
  are close to EdgarTools-caliber (structured, tagged, reconciling). Mexico,
  Colombia, and Peru are partial. Singapore has no structured alternative at
  all (heuristic PDF only). Canada is discovery-only - SEDAR+ has no stable
  automated filing-retrieval API.
- **Scope**: filing discovery, document conversion, financial-statement
  extraction, selected UK/India/Brazil insider and major-holder disclosures,
  and selected full-text/fact-series workflows are shipped. Broad institutional
  holdings, fund coverage, and cross-company comparison tooling remain open.
- **Reliability process**: scheduled live smoke and reviewed accuracy
  benchmarks cover keyless sources where the regulator permits automation;
  EDINET is search-only without a key and DART requires one. This is not yet
  continuous full-universe validation.

Closing these gaps - in roughly this order - is what "done" requires:
1. keep the smoke suite as the safety net while hardening the uneven markets;
2. research and add new markets using the keyless-open-data-portal pattern
   that has worked so far (a regulator-mandated bulk dataset or XBRL feed),
   not PDF scraping as the starting point;
3. only then, as a deliberate decision, consider expanding scope beyond
   filings and financials.
