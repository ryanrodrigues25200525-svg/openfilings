from __future__ import annotations

import pymupdf
import pytest

from openfilings.exceptions import ExtractionError
from openfilings.extraction.pdf import pdf_to_markdown


def test_pdf_to_markdown_extracts_text_from_memory() -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "OpenFilings annual report")
    pdf_bytes = document.tobytes()
    document.close()

    markdown = pdf_to_markdown(pdf_bytes)

    assert "OpenFilings annual report" in markdown


def test_pdf_to_markdown_rejects_non_pdf() -> None:
    with pytest.raises(ExtractionError, match="not a PDF"):
        pdf_to_markdown(b"plain text")
