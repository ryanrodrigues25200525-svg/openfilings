from __future__ import annotations

import subprocess

import pymupdf
import pytest

from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import ExtractionError
from openfilings.extraction.document import extract_document
from openfilings.extraction.ocr import ocr_pdf_to_markdown
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
