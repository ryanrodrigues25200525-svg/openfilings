"""PDF-to-Markdown conversion using a local, non-OCR fast path."""

from __future__ import annotations

from openfilings.exceptions import ExtractionError


def pdf_to_markdown(pdf_bytes: bytes) -> str:
    """Convert PDF bytes to Markdown without retaining a temporary source file."""

    if not pdf_bytes.startswith(b"%PDF"):
        raise ExtractionError("The source document is not a PDF.")

    try:
        # These imports initialize native PDF tooling, so keep them off the CLI's
        # startup path until a document actually needs conversion.
        import pymupdf
        import pymupdf4llm

        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            markdown = pymupdf4llm.to_markdown(document, use_ocr=False)
        finally:
            document.close()
    except Exception as exc:  # the underlying library exposes several error types
        raise ExtractionError(f"PDF-to-Markdown conversion failed: {exc}") from exc

    normalized = markdown.strip()
    if not normalized:
        raise ExtractionError("The PDF contained no natively extractable text")
    return normalized + "\n"
