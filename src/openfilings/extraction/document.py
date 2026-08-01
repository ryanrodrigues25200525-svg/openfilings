"""Quality-aware media routing for filing source documents."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from openfilings.adapters.base import SourceDocument
from openfilings.bmv_json import bmv_json_to_markdown
from openfilings.exceptions import ExtractionError
from openfilings.extraction.html import html_to_markdown
from openfilings.extraction.ocr import ocr_pdf_to_markdown, tesseract_available
from openfilings.extraction.pdf import pdf_to_markdown
from openfilings.extraction.pdf_detect import PdfClassification, classify_pdf
from openfilings.extraction.quality import add_quality_warning, assess_markdown
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import ExtractionQuality, OcrMode

_HTML_TYPES = {"text/html", "application/xhtml+xml"}
_ZIP_TYPES = {"application/zip", "application/x-zip-compressed"}
_MAX_ARCHIVE_FILES = 2_000


class OcrConverter(Protocol):
    def __call__(
        self,
        pdf_bytes: bytes,
        *,
        language: str,
        dpi: int,
        max_pages: int,
        executable: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    markdown: str
    method: str
    quality: ExtractionQuality


def extract_document(
    document: SourceDocument,
    *,
    pdf_converter: Callable[[bytes], str] = pdf_to_markdown,
    html_converter: Callable[[bytes], str] = html_to_markdown,
    ocr_converter: OcrConverter = ocr_pdf_to_markdown,
    ocr_available: Callable[[str], bool] = tesseract_available,
    ocr_mode: OcrMode = "auto",
    ocr_language: str = "eng",
    ocr_dpi: int = 200,
    ocr_max_pages: int = 250,
    ocr_executable: str = "tesseract",
    pdf_classifier: Callable[[bytes], PdfClassification | None] = classify_pdf,
) -> ExtractionResult:
    """Extract a document, score it, and route unusable PDFs to optional OCR."""

    media_type = document.media_type.casefold()
    if media_type == "application/pdf" or document.data.startswith(b"%PDF"):
        return _extract_pdf(
            document.data,
            pdf_converter=pdf_converter,
            ocr_converter=ocr_converter,
            ocr_available=ocr_available,
            ocr_mode=ocr_mode,
            ocr_language=ocr_language,
            ocr_dpi=ocr_dpi,
            ocr_max_pages=ocr_max_pages,
            ocr_executable=ocr_executable,
            pdf_classifier=pdf_classifier,
        )
    if media_type in _HTML_TYPES or _looks_like_html(document.data):
        markdown = html_converter(document.data)
        return ExtractionResult(
            markdown=markdown,
            method="markdownify",
            quality=assess_markdown(markdown),
        )
    if media_type in _ZIP_TYPES or document.data.startswith(b"PK\x03\x04"):
        if document.profile == "bmv-json":
            markdown = bmv_json_to_markdown(document.data)
            method = "bmv-xbrl-json"
        elif document.profile == "edinet":
            reports = html_documents_from_zip(document.data, public_documents_only=True)
            parts = [html_converter(report).strip() for report in reports]
            markdown = "\n\n---\n\n".join(part for part in parts if part)
            method = "edinet-zip-html+markdownify"
        else:
            html = main_html_from_zip(document.data)
            markdown = html_converter(html)
            method = "zip-html+markdownify"
        return ExtractionResult(
            markdown=markdown,
            method=method,
            quality=assess_markdown(markdown),
        )
    raise ExtractionError(f"Unsupported filing media type: {document.media_type}.")


def document_to_markdown(
    document: SourceDocument,
    *,
    pdf_converter: Callable[[bytes], str] = pdf_to_markdown,
    html_converter: Callable[[bytes], str] = html_to_markdown,
) -> tuple[str, str]:
    """Compatibility wrapper returning Markdown and extraction method."""

    result = extract_document(
        document,
        pdf_converter=pdf_converter,
        html_converter=html_converter,
        ocr_mode="never",
    )
    return result.markdown, result.method


def _extract_pdf(
    pdf_bytes: bytes,
    *,
    pdf_converter: Callable[[bytes], str],
    ocr_converter: OcrConverter,
    ocr_available: Callable[[str], bool],
    ocr_mode: OcrMode,
    ocr_language: str,
    ocr_dpi: int,
    ocr_max_pages: int,
    ocr_executable: str,
    pdf_classifier: Callable[[bytes], PdfClassification | None] = classify_pdf,
) -> ExtractionResult:
    if ocr_mode not in {"auto", "never", "always"}:
        raise ExtractionError(f"Unsupported OCR mode: {ocr_mode}.")

    page_count = _pdf_page_count(pdf_bytes)
    available = ocr_available(ocr_executable)
    classification = pdf_classifier(pdf_bytes)

    # A confident scanned/image-based classification means the fast path would
    # extract almost no text either way - assess_markdown("") already scores
    # 0/"unusable" and should_ocr below routes to OCR regardless, so this only
    # skips a doomed-to-fail pymupdf4llm pass, not a routing decision. Only
    # skip when OCR would actually run: otherwise the fast attempt is the only
    # chance at any text at all.
    skip_fast_path = (
        ocr_mode == "auto"
        and available
        and classification is not None
        and classification.likely_needs_ocr
    )
    fast_markdown: str | None = None
    fast_error: ExtractionError | None = None
    if not skip_fast_path:
        try:
            fast_markdown = pdf_converter(pdf_bytes)
        except ExtractionError as exc:
            fast_error = exc

    fast_quality = assess_markdown(fast_markdown or "", page_count=page_count)
    if classification is not None and classification.has_encoding_issues:
        # Diagnostic only - deliberately does not affect should_ocr. OCR has
        # no page-subset support (ocr_pdf_to_markdown OCRs every page), and
        # generally degrades table structure relative to correct native
        # extraction, so a partial font-encoding fault should not force a
        # whole-document OCR pass on evidence this narrow.
        fast_quality = add_quality_warning(fast_quality, "pdf_encoding_issues_detected")
    should_ocr = ocr_mode == "always" or (
        ocr_mode == "auto" and fast_quality.status == "unusable" and available
    )

    if ocr_mode == "always" and not available:
        raise ExtractionError(
            f"OCR was requested but '{ocr_executable}' is not installed or on PATH."
        )

    if should_ocr:
        try:
            ocr_markdown = ocr_converter(
                pdf_bytes,
                language=ocr_language,
                dpi=ocr_dpi,
                max_pages=ocr_max_pages,
                executable=ocr_executable,
            )
            ocr_quality = assess_markdown(ocr_markdown, page_count=page_count)
            if (
                ocr_mode == "auto"
                and fast_markdown
                and ocr_quality.score <= fast_quality.score
            ):
                return ExtractionResult(
                    markdown=fast_markdown,
                    method="pymupdf4llm",
                    quality=add_quality_warning(fast_quality, "ocr_not_better"),
                )
            return ExtractionResult(
                markdown=ocr_markdown,
                method="tesseract-ocr",
                quality=ocr_quality,
            )
        except ExtractionError:
            if fast_markdown is None:
                raise
            return ExtractionResult(
                markdown=fast_markdown,
                method="pymupdf4llm",
                quality=add_quality_warning(fast_quality, "ocr_failed"),
            )

    if fast_markdown is None:
        reason = (
            "OCR is disabled" if ocr_mode == "never" else "Tesseract is unavailable"
        )
        detail = str(fast_error) if fast_error else "native extraction produced no text"
        raise ExtractionError(f"{detail}; {reason}.") from fast_error

    if ocr_mode == "auto" and fast_quality.status == "unusable" and not available:
        fast_quality = add_quality_warning(fast_quality, "ocr_unavailable")
    return ExtractionResult(
        markdown=fast_markdown,
        method="pymupdf4llm",
        quality=fast_quality,
    )


def _pdf_page_count(pdf_bytes: bytes) -> int | None:
    try:
        import pymupdf

        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            return document.page_count
        finally:
            document.close()
    except Exception:
        return None


def main_html_from_zip(archive_bytes: bytes) -> bytes:
    """Return the largest XHTML/HTML report from a bounded filing archive."""
    reports = html_documents_from_zip(archive_bytes)
    return max(reports, key=len)


def html_documents_from_zip(
    archive_bytes: bytes, *, public_documents_only: bool = False
) -> tuple[bytes, ...]:
    """Return ordered, bounded HTML documents from a filing archive."""

    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) > _MAX_ARCHIVE_FILES:
                raise ExtractionError("The filing archive contains too many files.")
            if sum(member.file_size for member in members) > MAX_TAGGED_DOCUMENT_BYTES:
                raise ExtractionError("The filing archive is too large when expanded.")

            html_members = [
                member
                for member in members
                if member.filename.casefold().endswith((".xhtml", ".html", ".htm"))
            ]
            if public_documents_only:
                public_members = [
                    member
                    for member in html_members
                    if "/xbrl/publicdoc/"
                    in f"/{member.filename.casefold().lstrip('/')}"
                ]
                html_members = public_members or html_members
            if not html_members:
                raise ExtractionError("The filing archive contains no HTML report.")
            return tuple(
                archive.read(member)
                for member in sorted(
                    html_members, key=lambda item: item.filename.casefold()
                )
            )
    except ExtractionError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExtractionError(f"Could not read filing archive: {exc}") from exc


def _looks_like_html(data: bytes) -> bool:
    prefix = data[:512].lstrip().lower()
    return prefix.startswith((b"<!doctype html", b"<html", b"<?xml"))
