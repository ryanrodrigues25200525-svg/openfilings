# Future Integrations

Consolidated research on (1) which new markets are worth adding next and (2)
which EdgarTools feature categories OpenFilings still lacks, with an
implementation plan against the markets already shipped. See `GOAL.md` for
the scope this is measured against and `PROJECT_DESCRIPTION.md` for what's
currently live.

## 1. New markets, ranked

Same evidence bar used all session: "verified working" means a live
company/filing/financials round-trip was actually run; "documented" means a
public source was found but not exercised end-to-end; "dead end" means a
real blocker was confirmed (paywall, no public API, discontinued access).

### Tier A - clear keyless structured path, do these next

| Market | Regulator/source | Format | Status |
|---|---|---|---|
| Chile | CMF (Comisión para el Mercado Financiero) | XBRL (filer-submission taxonomy) | Downgraded this pass. CMF's public "Consulta de Estados Financieros" query tool (`sa_fecu_index.php`) is a legacy PHP form dating to the pre-IFRS Chilean-GAAP "FECU" regime, not a JSON API - it needs real POST-form reconnaissance (form field names, result-page shape) to find any per-company download link, and the IFRS-era query tool (`w4-propertyvalue-46324.html`) rendered no static content to inspect. CMF's own XBRL system (SEIL) is a **filer submission channel**, not a public read/download API - no evidence found this pass that submitted instances are re-exposed for public download anywhere. `cmfchile.cl` was also unreachable from this session's direct network tooling (DNS SERVFAIL, an environment issue, not a site block) - live-network inspection (the same technique that found ASX's and KAP's hidden JSON endpoints) is needed before this can be ranked Tier A again, and no build should start without it |

**Turkey shipped this pass** (see "Deprioritized or dead end" below - it's
no longer future work). Indonesia, Thailand, the Philippines, UAE (DFM/ADX),
Saudi Arabia, New Zealand, and Pakistan were dropped from this file by
request - none of them had any adapter code written against them (confirmed
by searching `src/` before removal), so nothing needed reverting. If any of
these markets come back into scope later, they need fresh research rather
than resurrecting this entry - the findings above are not preserved
elsewhere. **Re-research Chile with live-network tooling** before deciding
whether to build it at all.

### Deprioritized or dead end (carried over from earlier passes, unchanged)

- **Israel, South Africa** - explicitly deprioritized by user request, not a technical dead end.
- **Argentina, Malaysia** - confirmed not worth pursuing in the Chile/Argentina/Indonesia/Malaysia research pass (no accessible structured or reliably keyless PDF path at reasonable effort).
- **Qatar** - no public disclosure API or XBRL program found this pass; only market-news coverage. Not a lead.
- **Egypt** - dead end: EGX's disclosure/reports pages are behind a CAPTCHA bot-check (a hard blocker per this project's own rules against bypassing bot detection), and EGX only announced its XBRL rollout for 2025 - too immature even if the CAPTCHA weren't disqualifying on its own.
- **South Korea (DART)** - reconciled into `main` (rebased cleanly, all tests pass). Still not live-verified - no `DART_API_KEY` was available when built or reconciled. Live-verify against a real key before counting it as shipped.
- **Australia (ASX)** - reconciled into `main` (rebased cleanly, all tests pass, includes live-verified BHP/AQC extraction from the subagent's own session).
- **Turkey (KAP)** - shipped this pass. KAP's official Rest API data-distribution
  product is paid/contract-gated (a Borsa İstanbul agreement is required), but
  KAP's own public website (a Next.js SPA) is backed by plain, unauthenticated
  JSON endpoints - no bot-detection bypass involved (confirmed live: no CAPTCHA,
  no WAF challenge, plain 200 responses with a Referer header). "Finansal Rapor"
  financial-report disclosures embed the filer's full IFRS-tagged statements as
  pre-rendered HTML viewer tables with the literal XBRL concept
  (`ifrs-full_Assets`, `kap-fr_...`) next to each value, so the existing IFRS
  concept-alias table applies directly. Live-verified end-to-end (search,
  filings, financials, balance-sheet identity - exact match on both periods)
  against Deniz Gayrimenkul GYO, Turkcell, and BIM.
- **Germany** - not enabled; upstream `filings.xbrl.org` currently lists German filings as unavailable for reliable discovery (documented in `README.md` already, not a new finding).

## 2. EdgarTools feature parity beyond filings + financials

EdgarTools covers several SEC data categories OpenFilings has never
attempted, because GOAL.md's roadmap deliberately sequenced "more markets"
before "more data types per market." This section is that next layer,
scoped against what's actually feasible per already-shipped market - not a
blanket "add everything everywhere" plan.

| EdgarTools feature | SEC data it reads | Non-US equivalent concept | Status |
|---|---|---|---|
| Insider transactions | Forms 3/4/5 | Director/PDMR dealing notifications (EU MAR Art. 19, UK PDMR rules, India SEBI PIT) | **Shipped**: UK FCA NSM (`category="insider"` → NSM type code `DSH`), India NSE (`category="insider"` → SEBI PIT Regulation 7(2) disclosures, `/api/corporates-pit`), Brazil CVM (`category="insider"` → the yearly VLMO Open Data archive, CVM Instrução 358 art. 11). Live-verified against real companies on all three. Singapore SGX not shipped - see below |
| Institutional ownership / major shareholding | 13F-HR holdings reports | Major/substantial shareholding notifications (EU Transparency Directive, UK DTR5, India shareholding pattern) | **Shipped**: UK FCA NSM (`category="major_holdings"` → NSM type code `HOL`), India NSE (`category="major_holdings"` → periodic SEBI (LODR) Regulation 31 shareholding pattern, `/api/corporate-share-holdings-master`, includes a real downloadable XBRL document per filing). Brazil's VLMO combines this with insider trading in one filing, so it's covered by `category="insider"` there, not a separate category |
| Current reports | 8-K | Ad-hoc/regulatory news disclosures (UK RNS, EU ad-hoc disclosure, most exchanges' "material information" feed) | Not built. `filings()` already returns every disclosure type these feeds carry when `category` isn't `"accounts"`/`"insider"`/`"major_holdings"` - callers can filter client-side on `Filing.category`/`Filing.filing_type` today. A dedicated `category="current_report"` mapping per source (an explicit type-code allowlist, e.g. NSM's `UPD`/`ACQ`/`DIS`/`TST`/`BOA`) is a smaller, well-scoped follow-up, not attempted this pass |
| Proxy statements | DEF 14A | AGM/EGM notices and resolutions (published alongside annual reports on most exchanges) | Not built. Same shape as current reports - already present unfiltered in most feeds (NSM's `RAG`/`NOA`/`ROM` type codes, for example) |
| Fund/ETF data | N-PORT, N-CEN, fund holdings | Not a close match outside the US in most of these markets - fund regulation is typically separate from the listed-company regime this project covers. Lowest priority; treat as out of scope unless a specific market's fund disclosure regime is clearly public and keyless | Not built, not planned |
| Cross-company XBRL frames/comparison | `xbrl_frames` concept queries | Only meaningful once >1 market per region has consistent concept coverage - already true for the 13 ESEF countries via shared IFRS taxonomy concepts | Not built |

### What's shipped vs. still open, market by market

1. **UK FCA NSM** - done. `DSH` (Director/PDMR Shareholding) and `HOL`
   (Holding(s) in Company) confirmed live against NSM's own search results
   and added to `_NSM_CATEGORY_TYPE_CODES` in `service.py`. No new adapter
   code needed, exactly as predicted.
2. **India NSE** - done. `category="insider"` reads SEBI PIT disclosures
   (person name, transaction direction, share count, a real downloadable
   XBRL document) from `/api/corporates-pit`. `category="major_holdings"`
   reads periodic shareholding-pattern filings (promoter/public split, a
   real downloadable XBRL document) from `/api/corporate-share-holdings-master`.
   `NseClient.document_url()` was widened to also accept
   `/corporate/xbrl/*.xml`, not just `/annual_reports/*.pdf|.zip`.
3. **Brazil CVM** - done. `category="insider"` reads CVM's own yearly VLMO
   Open Data archive (`dados.cvm.gov.br/dados/CIA_ABERTA/DOC/VLMO/DADOS/`) -
   same row shape as the IPE archive already used for `category="accounts"`,
   reusing `_filing_from_row` and the existing `structured_archive()`
   fetch/cache path with zero new HTTP client code. The archive ships two
   CSVs per year (a per-company index and a much larger per-person detail
   file); only the index is parsed.
4. **Singapore SGX** - **not shipped, genuinely blocked this pass**. SGX's
   general corporate-announcements API (`api.sgx.com/corporateannouncements/v1.0`,
   found via the same technique that surfaced ASX's and KAP's endpoints)
   returns an AWS API Gateway `403 ForbiddenException` even with a plausible
   `Origin`/`Referer` - a real access-control block, not a route that simply
   doesn't exist (compare the "Missing Authentication Token" response from
   a genuinely wrong path). Per this project's own rule against bypassing
   bot/access-control detection, this was not pushed further. The
   `financialreports/v1.0` endpoint the existing `SgxClient` already calls
   only ever returns Annual Report / Sustainability Report items - it does
   not carry director's-interest or substantial-shareholder disclosures, so
   there was no cheaper path available inside the existing adapter.
5. **ESEF markets (13 countries)** - not attempted. Insider dealing and
   major-shareholding notifications are filed with each country's national
   Officially Appointed Mechanism (OAM), which is *not* the same feed as
   `filings.xbrl.org` (confirmed: `filings.xbrl.org` only ever carries
   ESEF annual/interim financial reports, nothing else - there is nothing
   to generalize a category filter over here). This is 13 separate research
   efforts, not one generic connector. Still the largest remaining lift;
   start with one country (Netherlands or France have the most mature OAM
   APIs) as a proof of concept before generalizing.
6. **Mexico, Peru, Colombia, Canada, Japan** - not yet researched for these
   feature categories; lowest priority until a reason to prioritize them
   comes up.

Current reports (8-K-equivalent) and AGM/proxy notices are, in practice,
already partially reachable today wherever `category="insider"`/
`"major_holdings"` now work (NSM, NSE, CVM): those feeds carry every other
disclosure type too, just not yet given their own named category. Building
that out is a smaller, separate follow-up (see the table above), not part
of this pass.

## Suggested sequencing

1. ~~Reconcile the South Korea DART and Australia/Switzerland subagent work
   into `main`~~ - done.
2. ~~Add Turkey~~ - done. Live-verified end-to-end.
3. ~~Generalize the category-filter pattern and add insider-trading/
   major-shareholding coverage for UK NSM, India NSE, and Brazil CVM~~ -
   done. Live-verified end-to-end on all three; Singapore SGX hit a genuine
   access-control block and was left unshipped rather than pushed through.
4. Indonesia, Thailand, and Chile are on hold, not needed right now -
   revisit with live-network reconnaissance when they matter again.
5. `category="current_report"`/`"proxy"` for NSM/NSE/CVM - the cheapest
   remaining EdgarTools-parity step, since it only needs a type-code
   allowlist per source, no new HTTP calls.
6. ESEF's 13 national OAMs for insider/major-shareholding, and
   Mexico/Peru/Colombia/Canada/Japan research for the same - largest
   remaining lift, lowest priority.
