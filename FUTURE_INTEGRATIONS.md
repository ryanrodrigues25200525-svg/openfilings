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
| Turkey | KAP (Public Disclosure Platform) | XBRL | Documented strong in earlier research pass this session; not yet live-verified end-to-end |
| Chile | CMF (Comisión para el Mercado Financiero) | XBRL | Documented strong in earlier research pass this session; not yet live-verified end-to-end |
| Indonesia | IDX financial-statements portal | Inline XBRL + `instance.zip` per company/quarter | New this pass - static per-issuer download URLs (`idx.co.id/.../{TICKER}/instance.zip`), no login on the file endpoints themselves. Same shape as the ESEF/`filings.xbrl.org` pattern that has worked repeatedly. Needs one live pull to confirm the instance documents use standard IFRS concept names (if so, the existing tagged-XBRL pipeline reuses with near-zero new mapping code, same as India/NSE and South Korea/DART) |
| Thailand | Thai SEC IDISC (`market.sec.or.th/public/idisc/`) | Raw filing ZIPs containing `FINANCIAL_STATEMENTS.XLS[X]` | New this pass - SET's own "SMART Marketplace" API is a paid subscription, but the underlying regulatory filings on SEC IDISC are the public disclosure record and appear directly downloadable (a third-party open dataset, `thaifin`, is built entirely from them with no auth). Needs confirmation that IDISC's own listing/search endpoints are keyless, not just the file host |

**Recommendation: add Turkey and Chile first** (highest confidence, already
researched twice), **then spend one research/build cycle each on Indonesia
and Thailand** to confirm the keyless path holds at the API layer, not just
the file layer.

### Tier B - PDF-only ceiling, feasible but lower priority

| Market | Regulator/source | Format | Status |
|---|---|---|---|
| Philippines | PSE EDGE (`edge.pse.com.ph`) | PDF only, no XBRL/API found | Same effort class as Singapore/Peru - keyless disclosure portal, PDF-heuristic extraction only. Worth adding once the Tier A markets are done, using the same "accept PDF as the ceiling" policy already applied to Singapore and Mexico's annual report |
| UAE (DFM) | Dubai Financial Market | PDF only, confirmed keyless document host | New this pass - real consolidated financial statements are directly downloadable with no login at `feeds.dfm.ae/documents/...` (verified against Emaar Properties, DFM itself, Al Ansari Financial Services, Dubai Taxi Company). The disclosures listing page is a JS single-page app, so the actual per-company listing API isn't visible from search - needs the same live-network-inspection approach that found ASX's hidden endpoint. UAE's regulator (SCA) has run mandatory XBRL filing since 2011 (IFRS-taxonomy-based, same shortcut that worked for India/DART), but the submission portal (`xbrl-uae.ae`) is filer-only (National ID + authorization letter required) - no public read/download endpoint for the raw instances was found, so treat as PDF-only until proven otherwise |
| UAE (ADX) | Abu Dhabi Securities Exchange | PDF only, plausible document host | Same regulator (SCA) as DFM. Disclosure/financial-report pages are JS SPAs; found a `adxservices.adx.ae/WebServices/DataServices/contentDownload.aspx?doc={id}` pattern suggesting a similar discoverable backend to DFM's, but not yet confirmed live |

### Tier C - needs one more targeted check before deciding

| Market | Regulator/source | Open question |
|---|---|---|
| Saudi Arabia | Tadawul Ifsah disclosure system | Ifsah tags filings in XBRL per Tadawul's own 2017 announcement, but the only structured *product* found (eReference Data) reads as a paid subscription. Unconfirmed whether Ifsah's underlying per-filing XBRL is separately public without eReference. One direct check against a live Ifsah filing page (not the eReference product page) needed before ranking this Tier A or dead end |
| New Zealand | NZX | NZX's own official data products (i-search, TransferHub) are subscription/license-gated with IP-whitelisted SFTP - not keyless. A third-party product ("NZXplorer") advertises a free API with iXBRL access, but it's a commercial reseller built on NZX's own PDFs (uses an LLM for extraction per its own docs), not an official public feed - same category as FinancialReports.eu, not a foundation to adapt against. The real open question, unresolved, is whether NZX's own public announcements page has an undocumented free JSON API the way ASX did - needs the same live-network-inspection approach, not more search-engine research |
| Pakistan | PSX (`dps.psx.com.pk`) | A real disclosure API exists (`/announcements`, `/symbols`, permanent-URL PDF documents), matching the pattern that has worked elsewhere. But the portal carries an explicit legal notice prohibiting "dissemination, transmission, sale, and commercial use of Market Data feed... without acquiring respective rights/license." Ambiguously worded (likely aimed at price/quote data, not regulatory disclosure PDFs) but real enough to need a legal read before building, not a clean green light like DFM |

### Deprioritized or dead end (carried over from earlier passes, unchanged)

- **Israel, South Africa** - explicitly deprioritized by user request, not a technical dead end.
- **Argentina, Malaysia** - confirmed not worth pursuing in the Chile/Argentina/Indonesia/Malaysia research pass (no accessible structured or reliably keyless PDF path at reasonable effort).
- **Qatar** - no public disclosure API or XBRL program found this pass; only market-news coverage. Not a lead.
- **Egypt** - dead end: EGX's disclosure/reports pages are behind a CAPTCHA bot-check (a hard blocker per this project's own rules against bypassing bot detection), and EGX only announced its XBRL rollout for 2025 - too immature even if the CAPTCHA weren't disqualifying on its own.
- **Australia, Switzerland** - a dedicated subagent researched and began building these; its worktree/branch (`agent-a70c89f4cfadcca12`) had not yet reported completion as of this writing. Check its output before re-researching from scratch.
- **South Korea (DART)** - already built by a subagent this session (adapter, structured extraction, tests - commit `8ce7bdc` in worktree `agent-a04b85a732487f085`), but **not yet reconciled into `main`** and not live-verified (the agent had no API key available). Reconcile using the same file-by-file cherry-pick-vs-manual-reapply process used for the Tier 0 ESEF worktree, then live-verify against a real DART key before counting it as shipped.
- **Germany** - not enabled; upstream `filings.xbrl.org` currently lists German filings as unavailable for reliable discovery (documented in `README.md` already, not a new finding).

## 2. EdgarTools feature parity beyond filings + financials

EdgarTools covers several SEC data categories OpenFilings has never
attempted, because GOAL.md's roadmap deliberately sequenced "more markets"
before "more data types per market." This section is that next layer,
scoped against what's actually feasible per already-shipped market - not a
blanket "add everything everywhere" plan.

| EdgarTools feature | SEC data it reads | Non-US equivalent concept |
|---|---|---|
| Insider transactions | Forms 3/4/5 | Director/PDMR dealing notifications (EU Market Abuse Regulation Art. 19, UK PDMR rules, India SEBI PIT, Singapore SGXNet) |
| Institutional ownership (13F) | 13F-HR holdings reports | Major/substantial shareholding notifications (EU Transparency Directive, UK DTR5, India SAST) |
| Current reports | 8-K | Ad-hoc/regulatory news disclosures (UK RNS, EU ad-hoc disclosure, most exchanges' "material information" feed) |
| Proxy statements | DEF 14A | AGM/EGM notices and resolutions (published alongside annual reports on most exchanges) |
| Fund/ETF data | N-PORT, N-CEN, fund holdings | Not a close match outside the US in most of these markets - fund regulation is typically separate from the listed-company regime this project covers. Lowest priority; treat as out of scope unless a specific market's fund disclosure regime is clearly public and keyless |
| Cross-company XBRL frames/comparison | `xbrl_frames` concept queries | Only meaningful once >1 market per region has consistent concept coverage - already true for the 13 ESEF countries via shared IFRS taxonomy concepts |

### Implementation plan, market by market (current 22 jurisdictions)

Ranked by how directly the existing adapter/pipeline extends, not by market
importance:

1. **ESEF markets (13 countries)** - insider dealing and major-shareholding
   notifications are filed with each country's national Officially Appointed
   Mechanism (OAM), which is *not* the same feed as `filings.xbrl.org`. This
   is 13 separate research efforts, not one generic connector - the ESEF
   shortcut that made statement extraction free (shared IFRS concepts) does
   not apply here. Start with one country (Netherlands or France have the
   most mature OAM APIs) as a proof of concept before generalizing.
2. **UK FCA NSM** - the existing `FcaNsmClient` already lists disclosures by
   type code (this session's `category="accounts"` → `"ACS"` fix touched
   exactly this mechanism). PDMR dealing notices and major-shareholding
   (TR-1) notices are separate NSM type codes on the *same* feed already
   being polled - this is the lowest-effort extension available: add
   `category="insider"`/`category="major_holdings"` mapped to their NSM type
   codes, no new adapter needed.
3. **India NSE** - insider trading (SAST/PIT) and shareholding-pattern
   disclosures are published through NSE's existing corporate-announcements
   API, the same family of endpoints the Integrated Filing XBRL adapter
   already calls. Second-lowest effort.
4. **Brazil CVM** - CVM Open Data (the same bulk-dataset pattern already used
   for DFP/ITR financials) separately publishes insider-trading and
   shareholding-notification datasets. Reuses the existing CVM Open Data
   client shape.
5. **Singapore SGX** - director/substantial-shareholder dealings are
   disclosed via SGXNet, PDF/HTML only (no structured feed found for these
   either) - same PDF-heuristic-as-ceiling policy that already applies to
   Singapore's financials.
6. **Mexico, Peru, Colombia, Canada, Japan** - not yet researched for these
   feature categories; lowest priority until the markets above are done.

8-K-equivalent "current report" coverage and AGM/proxy notices are, in
practice, already partially reachable today for every keyless source: most
regulator feeds (NSM, ESEF's `filings.xbrl.org`, CVM Open Data, NSE) list
*all* disclosure types, not just financial statements - `filings()` already
returns them, they're just not yet filtered/labeled by category the way
`category="accounts"` filters UK filings today. The fastest first step
across every market is generalizing that existing category-filter pattern
(built for UK this session) to expose whatever non-financial disclosure
types each source's feed already contains, before building any new
adapters.

## Suggested sequencing

1. Reconcile the South Korea DART and Australia/Switzerland subagent work
   into `main` (unblocks nothing else, but has been sitting unmerged).
2. Add Turkey and Chile (Tier A, highest confidence).
3. Research-verify Indonesia and Thailand at the API layer, then add.
4. Generalize the UK category-filter pattern to surface non-financial
   disclosure types already present in existing feeds (NSM, ESEF, CVM, NSE) -
   this is the cheapest step toward EdgarTools' insider/ownership/current-report
   parity and touches zero new markets.
5. Only then take on new insider-trading/shareholding adapters per market,
   ESEF's 13 national OAMs being the largest remaining lift.
