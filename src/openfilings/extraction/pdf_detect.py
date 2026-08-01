"""Optional pre-extraction PDF classification via the ``pdf-inspector`` package.

Scope is deliberately narrow. ``pdf-inspector`` was evaluated as a wholesale
replacement for ``pymupdf4llm`` and rejected for that role: on every filing
tested (Unilever, DBS, Keppel) the two engines produced identical figures,
and the existing heuristic table parser in ``xbrl/pdf_statements.py`` does
not recognize ``pdf-inspector``'s markdown dialect at all - feeding its
output in unmodified raises ``FinancialsUnavailableError``. Swapping engines
would break every PDF-tier market until that parser were rewritten, on no
evidence of a correctness gain.

What *is* a genuine, evidence-backed win: ``pdf-inspector``'s classification
step is a real, structural pre-check (10-50ms) that today's document-level,
text-density heuristic (``quality.assess_markdown``) cannot see:

- a document confidently classified as scanned/image-based will extract to
  almost no text either way, so skipping the pymupdf4llm attempt is pure
  work avoidance, not a behavior change - ``assess_markdown`` would already
  route to OCR in that case.
- ``has_encoding_issues`` flags a broken CID/ToUnicode font map structurally,
  which is a different and more direct signal than inferring "encoding
  trouble" from a replacement-character ratio after the fact. This is
  surfaced as a diagnostic warning only, not used to force OCR: OCR
  generally degrades table structure relative to correct native extraction,
  and there is no page-subset OCR support (``ocr_pdf_to_markdown`` OCRs
  every page), so forcing whole-document OCR on a partial encoding fault
  could make a filing worse rather than better.

The dependency is optional (``pdf-detect`` extra) and every function here
degrades to "no opinion" when it is not installed or the call fails for any
reason, so nothing in the default install path changes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PdfClassification:
    likely_needs_ocr: bool
    has_encoding_issues: bool


def classify_pdf(pdf_bytes: bytes) -> PdfClassification | None:
    """Return a fast pre-extraction classification, or None if unavailable."""

    try:
        import pdf_inspector
    except ImportError:
        return None
    try:
        result = pdf_inspector.detect_pdf_bytes(pdf_bytes)
    except Exception:
        return None
    return PdfClassification(
        likely_needs_ocr=result.pdf_type in {"scanned", "image_based"},
        has_encoding_issues=bool(result.has_encoding_issues),
    )
