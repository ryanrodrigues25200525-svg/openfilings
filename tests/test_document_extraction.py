from __future__ import annotations

import io
import zipfile

import pytest

from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import ExtractionError
from openfilings.extraction.document import document_to_markdown
from openfilings.extraction.html import html_to_markdown


def test_html_to_markdown_preserves_headings_links_and_tables() -> None:
    html = b"""
    <html><head><style>p { color: red }</style></head><body>
      <img src="tracker.gif" width="1" height="1">
      <div>RNS Number: 1234</div><div>Example PLC</div>
      <h1>Half-year results</h1>
      <p>Revenue was <strong>higher</strong>.</p>
      <table><tr><th>Metric</th><th>2026</th></tr>
      <tr><td>Revenue</td><td>100</td></tr></table>
    </body></html>
    """

    markdown = html_to_markdown(html)

    assert "# Half-year results" in markdown
    assert "RNS Number: 1234\n\nExample PLC" in markdown
    assert "**higher**" in markdown
    assert "| Metric | 2026 |" in markdown
    assert "tracker.gif" not in markdown


def test_zip_dispatch_uses_largest_xhtml_report() -> None:
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("index.xhtml", "<html><body>Annual report body</body></html>")
        archive.writestr("small.html", "<html>x</html>")

    markdown, method = document_to_markdown(
        SourceDocument(
            data=archive_bytes.getvalue(),
            media_type="application/zip",
            source_url="https://example.test/report.zip",
        )
    )

    assert "Annual report body" in markdown
    assert method == "zip-html+markdownify"


def test_unknown_document_type_is_rejected() -> None:
    with pytest.raises(ExtractionError, match="Unsupported"):
        document_to_markdown(
            SourceDocument(
                data=b"binary",
                media_type="application/octet-stream",
                source_url="https://example.test/document.bin",
            )
        )
