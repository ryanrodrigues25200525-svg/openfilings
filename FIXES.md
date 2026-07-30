# Fixes

A working checklist of known defects and gaps, each with the fix that closes
it. Items with a `#n` link have a GitHub issue carrying the full reproduction;
the rest were found while auditing this file into existence and are recorded
here first.

Nothing below is speculative. Every entry was reproduced against the live
source, observed in the code, or measured — this is not a wishlist.

Ordering is by what blocks real use, not by effort.

---

## P1 — Research blockers

The extraction layer is sound, but the normalized model stops one layer short
of the metrics valuation work actually uses. Today's 31 line items cover the
primary statements' totals and little else.

- [ ] **No depreciation & amortisation → EBITDA is not computable.**
  *Fix:* add a `depreciation_amortisation` entry to `LINE_ITEMS`
  (`DepreciationAndAmortisationExpense`,
  `DepreciationAmortisationAndImpairmentLoss…`), mapped on the cash-flow
  statement where it is most reliably tagged as the add-back.

- [ ] **No debt or borrowings → net debt, EV and leverage are not computable.**
  *Fix:* add `short_term_debt` (`ShorttermBorrowings`,
  `CurrentPortionOfLongtermBorrowings`) and `long_term_debt`
  (`LongtermBorrowings`), then derive `total_debt` with the existing
  `_sum_line_items` machinery so it carries `provenance: derived` honestly.

- [ ] **No non-controlling interests → the EV bridge is incomplete.**
  *Fix:* add `noncontrolling_interests` aliasing `NoncontrollingInterests`.
  Already present in source filings — it is how the equity/NCI defect was
  diagnosed — so this is purely a mapping addition.

- [ ] **No share counts → no market cap, no per-share metric, no dilution.**
  *Fix:* add `shares_outstanding` and `weighted_average_shares_diluted`.
  **Handle separately from the items above**: a share count is not money, and
  a currency scale multiplier applied to it reproduces the India `crore` bug
  exactly. `pdf_statements` already special-cases EPS with `item_scale = 1`;
  share counts need the same treatment plus a non-currency unit.

- [ ] **No trade payables or lease liabilities.**
  *Fix:* add `trade_payables` (`TradeAndOtherCurrentPayables`) and
  `lease_liabilities` (current + non-current). Unlocks working-capital and
  post-IFRS-16 adjusted leverage.

> **Constraint for all of the above.** Do **not** map these new sub-items into
> `validation.py`. That module documents the reason: finvariant treats a
> partially-supplied section as *not footing* rather than skipping it, so
> adding a few components without every sibling produces false validation
> failures. Extract and expose them; leave the identity mapping alone.

- [ ] **Cross-market comparison has no FX.** A Turkish issuer in TRY cannot be
  compared with a Finnish one in EUR.
  *Fix:* **deliberately do not build this.** Correct conversion needs
  period-end rates for the balance sheet, period-average for the P&L, and a
  rate source with history — a subsystem in its own right, and a second-rate
  version inside a filings library silently produces wrong comps. Guarantee
  `unit` is always populated and document the join to a dedicated rates
  source instead.

- [ ] **Segment and geography revenue is captured but not reachable.**
  *Fix:* this is a surfacing problem, not extraction — `dimensions` are
  already preserved on every `FinancialValue`, so the XBRL axes are in the
  data now. Add an `include_dimensions` flag to `filing_financials` and a
  group-by-axis helper. Expose raw axis/member pairs first; normalizing
  member names across taxonomies is hard and must not block access.

- [ ] **No screening — "every EU issuer under 8× EBITDA" is impossible.**
  *Fix:* do not build a query engine. `historical_backfill` already persists
  facts to SQLite; add (a) a bounded bulk backfill across a market's issuer
  list and (b) Parquet/CSV export (the `dataframe` extra already exists).
  Screening then happens in DuckDB or pandas.

---

## P2 — Correctness

- [ ] **[#6](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/6) Unilever's Form 20-F extracts a wrong currency (PEN for a UK issuer) and a ~1000× scale.**
  *Fix:* treat the currency misdetection as its own bug first — a filing
  retrieved from the FCA's UK storage mechanism should never resolve to PEN,
  and a source-plausibility check is cheap and independent of the table
  parsing. Do not rewrite the PDF heuristics wholesale to satisfy one
  document. Note the failure is already loud: `validation.ok` is `false`.

- [ ] **Partial failures are invisible on `source="all"`.** `_gather_available`
  returns whatever succeeded and discards every exception whenever at least
  one call worked. If 13 of 14 ESEF markets are down, the caller sees a short
  result list and no indication anything failed — which reads as "no such
  company" rather than "the data is incomplete." For research this is worse
  than an error.
  *Fix:* return the failures alongside the results and surface them in the
  MCP envelope (a `partial: true` plus the failing sources), so a caller can
  distinguish "not found" from "not checked."

- [ ] **[#12](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/12) One LEI can produce two company IDs across jurisdictions.**
  *Fix:* decide deliberately between deduplicating search results by LEI
  (preferring the home country), making the LEI the company ID and moving
  country onto the filing, or documenting the duplication as intended. It is
  currently true by accident.

- [ ] **[#14](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/14) Some ESEF issuers return a years-old "latest" filing.**
  *Fix:* first establish which explanation applies — for one affected issuer,
  compare what filings.xbrl.org returns for the LEI *without* the country
  filter against what the adapter returns *with* it. That single comparison
  separates an upstream gap from a discovery bug, and only the second is ours.

---

## P3 — Verification

- [ ] **[#7](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/7) The Sweden and Singapore regression guards cannot fire.**
  *Fix:* pin reference facts in `benchmarks.py`, transcribed by hand from the
  published reports. That file's own rule applies — never populate it from
  extractor output.

- [ ] **[#8](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/8) Pinned benchmarks cover 2 issuers across 25 markets.**
  *Fix:* one reviewed issuer per distinct extraction path (CVM Open Data,
  SFC CUIF, NSE XBRL, KAP viewer, BMV, SMV, PDF-heuristic), prioritising
  paths where a scaling or concept-mapping error would still reconcile.

- [ ] **The 8 "extracted but unverifiable" markets prove nothing today.**
  Where a total is derived, the accounting identity is circular and correctly
  skipped.
  *Fix:* footing is **not** circular — `current + noncurrent liabilities`
  against the derived total uses independently tagged components, and
  finvariant already has the rule (`FOOT.bs.total_liabilities`, which is what
  fired on Unilever). Report the footing outcome instead of a bare
  `not_applicable`. Report rather than fail at first: legitimate presentation
  differences exist (Volvo books provisions separately), so observe the
  distribution before enforcing.

- [ ] **[#9](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/9) The multi-issuer probe is not repeatable.**
  *Fix:* commit a bounded check — 2–3 reviewed issuers per market, none of
  them the smoke issuer — reporting verified/unverifiable/failed rather than
  pass/fail. Schedule monthly, not weekly. Keep it polite: these are free
  public endpoints.

- [ ] **[#11](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/11) South Korea has never run against a live DART key.**
  *Fix:* register a free key, resolve a KOSPI issuer, list filings, extract
  statements, check the identity. Then either drop the caveat or file what
  broke. Until then the caveat stays.

---

## P4 — Usability and operations

- [ ] **[#10](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/10) Brand and ticker names do not resolve (`PKO`, `Ford Otosan`).**
  *Fix:* two separable pieces. (1) `ranked_matches` requires the query to be
  a *substring* of a name field, so an extra token absent from the legal name
  kills an otherwise strong match — a token-subset or best-partial score
  fixes this class with no new data. (2) `PKO` appears nowhere in the legal
  name and needs an alias source per adapter. Do (1); treat (2) as
  per-source. Any change here needs near-miss tests, not just happy paths —
  loosening the matcher risks false positives across thousands of issuers.

- [ ] **[#15](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/15) Cached facts and a running MCP server both survive a fix.**
  *Fix:* stamp cached financials with an extractor version and treat a
  mismatch as a cache miss. That makes corrections propagate on their own and
  subsumes the alternatives (a cache-invalidation command, changelog notes).

- [ ] **The README documents 12 of 22 MCP tools.** Undocumented:
  `data_quality_report`, `financials_query`, `historical_facts_query`,
  `historical_backfill`, `companies_compare`, `company_research_brief`,
  `filings_diff`, `insider_dealings_list`, `watchlist_check`,
  `filing_sections`. `data_quality_report` is the tool an agent needs to know
  whether to trust a figure, and the fact-series tools are the ones research
  workflows depend on.
  *Fix:* generate the tool list from `server.py` at docs-build or test time,
  so it cannot drift again. A test asserting the README lists every
  `@mcp.tool()` is the cheap version.

- [ ] **[#13](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/13) Every Dependabot PR fails CI because `uv.lock` is not regenerated.**
  *Fix:* add a lockfile-refresh step so the PRs are testable, or restrict
  Dependabot to security updates and bump by hand with `uv lock --upgrade`.
  Do not drop `--locked` from CI — it is what makes the build reproducible.
  Separately, comment the deliberate `mcp<2` pin in `pyproject.toml` so it is
  not "helpfully" widened later.

---

## P5 — Packaging and distribution

The package cannot currently be installed by anyone who is not cloning the
repository, and ships less metadata than a PyPI page needs.

- [ ] **No `py.typed` marker.** The public API is fully annotated, but with no
  marker downstream type checkers ignore all of it.
  *Fix:* add `src/openfilings/py.typed` and include it in the wheel via
  `[tool.hatch.build]`.

- [ ] **No `__version__` exported.** Callers cannot introspect the installed
  version, which also blocks the cache-stamping fix above.
  *Fix:* expose `__version__` in `__init__.py` from package metadata.

- [ ] **No PyPI metadata.** `pyproject.toml` has no `classifiers`, no
  `keywords`, no `[project.urls]`, and no license field, so a published page
  would be bare and unsearchable.
  *Fix:* add classifiers (Python versions, licence, topic), keywords, and
  URLs for homepage, source and issues.

- [ ] **No release has been cut.** Version is pinned at `0.21.0` with a large
  `Unreleased` changelog and zero git tags, so "latest" is undefined.
  *Fix:* cut a tagged release once P1/P2 land, and add a release workflow
  that builds and publishes on tag.

---

## P6 — Robustness

- [ ] **The SQLite cache has no schema version.** Tables are created with
  `CREATE TABLE IF NOT EXISTS` and there is no `user_version` or migration
  path, so a schema change silently meets an old database.
  *Fix:* set `PRAGMA user_version`, check it on open, and either migrate or
  rebuild on mismatch. Pairs directly with the extractor-version stamp in
  [#15](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/15).

- [ ] **Retry backoff has no jitter.** `_common.py` backs off on a fixed
  `0.25 · 2^n` / `0.5 · 2^n` schedule. A `source="all"` search fans out to
  every adapter at once, so a shared upstream hiccup retries them all in
  lockstep.
  *Fix:* add proportional jitter. Small change, and it matters because these
  are free public regulator endpoints that deserve polite behaviour.

---

## Not defects

Recorded so they are not "rediscovered" later:

- **Bancolombia does not resolve.** It reorganised under Grupo Cibest S.A. in
  2025; the library correctly returns the current listed issuer.
- **Cementos Pacasmayo does not resolve.** Genuinely absent from SMV's summary
  dataset upstream.
- **Telenor Danmark is a separate record from Telenor ASA.** Different LEIs,
  correctly distinct — unlike [#12](https://github.com/ryanrodrigues25200525-svg/openfilings/issues/12), which is one LEI twice.
- **Germany is unavailable.** Blocked upstream: filings.xbrl.org lists German
  filings as unavailable for reliable discovery.
- **The hardcoded key in `adapters/sfc.py`.** It is the public client
  identifier the SIMEV frontend ships to every browser, documented as such in
  a comment. Secret scanners will flag it; it is a false positive.
