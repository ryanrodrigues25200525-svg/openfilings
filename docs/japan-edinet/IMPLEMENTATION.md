# Japan EDINET Implementation

## Phase 1: Source and Identity

- [x] Extend normalized models for Japan and EDINET.
- [x] Parse the official CP932 issuer-code ZIP safely.
- [x] Add search by Japanese/English name, EDINET code, and ticker.
- [x] Add EDINET v2 date-list retrieval with pacing and retries.
- [x] Normalize document types and at least 120 days of history.

## Phase 2: Documents and Financials

- [x] Download bounded EDINET type-1 filing packages.
- [x] Combine ordered `XBRL/PublicDoc` files into clean Markdown.
- [x] Add JP-GAAP aliases to the shared statement mapper.
- [x] Parse multi-document Inline XBRL into cached statements.

## Phase 3: Product Integration

- [x] Add service, CLI, MCP, settings, and cache routing.
- [x] Add offline fixtures and integration tests.
- [x] Document API-key registration and examples.
- [x] Run lint, formatting, tests, compilation, and startup checks.

## Success Criteria

Japan satisfies the market entry criteria: company search, normalized filing
history of at least three months, Markdown content, and structured statements
where EDINET supplies XBRL.
