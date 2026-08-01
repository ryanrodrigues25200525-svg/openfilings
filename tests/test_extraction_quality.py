from __future__ import annotations

import subprocess

import pymupdf
import pytest

from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import ExtractionError
from openfilings.extraction.document import extract_document
from openfilings.extraction.ocr import ocr_pdf_to_markdown
from openfilings.extraction.pdf_detect import PdfClassification
from openfilings.extraction.quality import assess_markdown


def test_quality_scoring_explains_good_and_unusable_content() -> None:
    good = assess_markdown(
        ("Revenue increased to 100 million during the financial year.\n" * 20),
        page_count=2,
    )
    unusable = assess_markdown("", page_count=4)

    assert good.status == "good"
    assert good.score == 100
    assert good.characters_per_page is not None
    assert unusable.status == "unusable"
    assert "no_text" in unusable.warnings
    assert "very_low_text_per_page" in unusable.warnings


def test_auto_mode_routes_scanned_pdf_to_ocr() -> None:
    pdf_bytes = _scanned_pdf_bytes()
    ocr_calls = 0

    def fake_ocr(
        _: bytes,
        *,
        language: str,
        dpi: int,
        max_pages: int,
        executable: str,
    ) -> str:
        nonlocal ocr_calls
        ocr_calls += 1
        assert (language, dpi, max_pages, executable) == (
            "eng",
            200,
            250,
            "tesseract",
        )
        return "## Page 1\n\n" + ("Scanned annual report revenue 100. " * 20)

    result = extract_document(
        SourceDocument(
            data=pdf_bytes,
            media_type="application/pdf",
            source_url="https://example.test/scanned.pdf",
        ),
        ocr_converter=fake_ocr,
        ocr_available=lambda _: True,
    )

    assert ocr_calls == 1
    assert result.method == "tesseract-ocr"
    assert result.quality.status == "good"
    assert "Scanned annual report" in result.markdown


def test_auto_mode_reports_when_ocr_is_unavailable() -> None:
    result = extract_document(
        SourceDocument(
            data=_scanned_pdf_bytes(),
            media_type="application/pdf",
            source_url="https://example.test/report.pdf",
        ),
        pdf_converter=lambda _: "tiny\n",
        ocr_available=lambda _: False,
    )

    assert result.method == "pymupdf4llm"
    assert result.quality.status == "unusable"
    assert "ocr_unavailable" in result.quality.warnings


def test_confident_scanned_classification_skips_the_wasted_fast_attempt() -> None:
    """A pdf-inspector classification of scanned/image-based means the fast
    path would extract almost no text either way, so it is skipped as pure
    work avoidance - not a new routing decision. Confirm the pdf_converter is
    never even called, and the outcome (OCR runs, its text wins) is identical
    to what today's code already does when the fast path is attempted and
    scores unusable."""

    fast_path_calls = 0

    def fast_path_that_must_not_run(_: bytes) -> str:
        nonlocal fast_path_calls
        fast_path_calls += 1
        return "should never be called"

    result = extract_document(
        SourceDocument(
            data=b"%PDF-test-fixture",
            media_type="application/pdf",
            source_url="https://example.test/scanned.pdf",
        ),
        pdf_converter=fast_path_that_must_not_run,
        ocr_converter=lambda *_a, **_k: "OCR recovered this text about revenue.\n" * 20,
        ocr_available=lambda _: True,
        pdf_classifier=lambda _: PdfClassification(
            likely_needs_ocr=True, has_encoding_issues=False
        ),
    )

    assert fast_path_calls == 0
    assert result.method == "tesseract-ocr"
    assert "OCR recovered" in result.markdown


def test_scanned_classification_does_not_skip_the_fast_path_without_ocr() -> None:
    """If OCR is unavailable, the fast attempt must still run - it is the
    only chance at any text at all, even a "scanned" classification must not
    skip it in that case."""

    result = extract_document(
        SourceDocument(
            data=b"%PDF-test-fixture",
            media_type="application/pdf",
            source_url="https://example.test/scanned.pdf",
        ),
        pdf_converter=lambda _: "Best-effort native text.\n" * 20,
        ocr_available=lambda _: False,
        pdf_classifier=lambda _: PdfClassification(
            likely_needs_ocr=True, has_encoding_issues=False
        ),
    )

    assert result.method == "pymupdf4llm"
    assert "Best-effort native text" in result.markdown


def test_encoding_issue_classification_adds_a_warning_without_forcing_ocr() -> None:
    """has_encoding_issues is diagnostic only. OCR has no page-subset support
    and generally degrades table structure, so a partial font-encoding fault
    must not force a whole-document OCR pass - the fast path's own quality
    score still decides routing."""

    ocr_calls = 0

    def ocr_must_not_run(*_a, **_k) -> str:
        nonlocal ocr_calls
        ocr_calls += 1
        return "unused"

    good_text = "Revenue increased to 100 million during the financial year.\n" * 20
    result = extract_document(
        SourceDocument(
            data=b"%PDF-test-fixture",
            media_type="application/pdf",
            source_url="https://example.test/report.pdf",
        ),
        pdf_converter=lambda _: good_text,
        ocr_converter=ocr_must_not_run,
        ocr_available=lambda _: True,
        pdf_classifier=lambda _: PdfClassification(
            likely_needs_ocr=False, has_encoding_issues=True
        ),
    )

    assert ocr_calls == 0
    assert result.method == "pymupdf4llm"
    assert result.markdown == good_text
    assert "pdf_encoding_issues_detected" in result.quality.warnings


def test_missing_pdf_classifier_dependency_leaves_behavior_unchanged() -> None:
    """classify_pdf returns None when pdf-inspector is not installed (the
    default install has no such dependency). Confirm that degrades to
    exactly today's behavior rather than raising or changing routing."""

    from openfilings.extraction.pdf_detect import classify_pdf

    assert classify_pdf(b"%PDF-anything") is None

    result = extract_document(
        SourceDocument(
            data=b"%PDF-test-fixture",
            media_type="application/pdf",
            source_url="https://example.test/report.pdf",
        ),
        pdf_converter=lambda _: "Native text\n" * 20,
        ocr_available=lambda _: True,
    )

    assert result.method == "pymupdf4llm"
    assert "pdf_encoding_issues_detected" not in result.quality.warnings


def test_always_mode_requires_tesseract() -> None:
    with pytest.raises(ExtractionError, match="not installed"):
        extract_document(
            SourceDocument(
                data=b"%PDF-test-fixture",
                media_type="application/pdf",
                source_url="https://example.test/report.pdf",
            ),
            pdf_converter=lambda _: "Native text\n",
            ocr_available=lambda _: False,
            ocr_mode="always",
        )


def test_tesseract_streams_rendered_png_to_runner(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_which(_: str) -> str:
        return "/usr/local/bin/tesseract"

    def fake_runner(
        command: list[str], image_bytes: bytes, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        observed.update(command=command, image=image_bytes, timeout=timeout)
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=b"Recognised annual report text",
            stderr=b"",
        )

    monkeypatch.setattr("openfilings.extraction.ocr.shutil.which", fake_which)
    markdown = ocr_pdf_to_markdown(
        _scanned_pdf_bytes(),
        command_runner=fake_runner,
    )

    assert observed["command"] == [
        "/usr/local/bin/tesseract",
        "-",
        "-",
        "-l",
        "eng",
        "--psm",
        "3",
        "quiet",
    ]
    assert bytes(observed["image"]).startswith(b"\x89PNG")
    assert observed["timeout"] == 120.0
    assert "## Page 1" in markdown
    assert "Recognised annual report text" in markdown


def _scanned_pdf_bytes() -> bytes:
    source = pymupdf.open()
    source_page = source.new_page(width=400, height=200)
    source_page.insert_text((40, 80), "Scanned annual report revenue 100")
    image = source_page.get_pixmap(dpi=150, alpha=False).tobytes("png")
    source.close()

    scanned = pymupdf.open()
    scanned_page = scanned.new_page(width=400, height=200)
    scanned_page.insert_image(scanned_page.rect, stream=image)
    pdf_bytes = scanned.tobytes()
    scanned.close()
    return pdf_bytes
