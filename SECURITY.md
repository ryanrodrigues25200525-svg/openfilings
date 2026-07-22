# Security policy

## Supported versions

Security fixes are applied to the current minor release on `main`. Older minor
releases should be upgraded before reporting an issue as unresolved.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Once this repository
is hosted, use its private security-advisory channel and include the affected
version, reproduction steps, impact, and any proposed mitigation. Do not include
real regulator credentials, personal data, or unpublished filing material.

## Security boundaries

OpenFilings accepts untrusted public documents. Downloads are size-limited,
archive paths are validated, XML parsing is bounded, URLs are restricted to
expected regulator hosts, and OCR has page and per-page timeout limits. Source
documents are processed locally and discarded; only normalized metadata,
compressed Markdown, and structured financials are cached.

EDINET credentials are read from the environment and must never be committed.
The keyless scheduled smoke suite deliberately excludes Japan.
