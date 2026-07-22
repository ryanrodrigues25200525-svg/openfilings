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


def test_html_to_markdown_recovers_repeated_navigation_as_headings() -> None:
    navigation = """
      <table><tr>
        <td><a href="#strategy">Strategic report</a></td>
        <td><a href="#financials">Financial statements</a></td>
        <td><span>Financial p</span><a href="#performance">erformance</a></td>
      </tr></table>
    """
    html = f"""
    <html><body>
      <h2>Existing tagged heading</h2><h3>Existing subsection</h3>
      {navigation}{navigation}{navigation}
      <div id="strategy"></div><div>Our strategy and business model.</div>
      <div id="financials"></div><div>Income statement and balance sheet.</div>
      <div id="performance"></div><div>Revenue and margin performance.</div>
    </body></html>
    """.encode()

    markdown = html_to_markdown(html)

    assert "## Strategic report" in markdown
    assert "## Financial statements" in markdown
    assert "## Financial performance" in markdown
    assert "## Financial p erformance" not in markdown
    assert markdown.index("## Strategic report") < markdown.index("Our strategy")
    assert markdown.index("## Financial statements") < markdown.index(
        "Income statement"
    )


def test_html_to_markdown_does_not_promote_numeric_page_links() -> None:
    navigation = '<a href="#page-12">12</a>' * 5
    html = (
        f"<html><body>{navigation}<div id='page-12'></div>"
        "<p>Page text</p></body></html>"
    ).encode()

    markdown = html_to_markdown(html)

    assert "## 12" not in markdown


def test_html_to_markdown_recovers_large_styled_text_as_sections() -> None:
    html = b"""
    <html><head><style>
      .fs-body{font-size:30px}.fs-metric{font-size:76px}
      .fs-section{font-size:92px}.fs-title{font-size:136px}
    </style></head><body>
      <div class="t fs-body">Ordinary narrative text.</div>
      <div class="t fs-section">Financial</div>
      <div class="t fs-section">statements</div>
      <div class="t fs-body">Statement introduction.</div>
      <div class="t fs-metric">Revenue EUR millions</div>
      <div class="t fs-title">Risk management Risk management</div>
      <table><tr><td><div class="t fs-title">Table label</div></td></tr></table>
    </body></html>
    """

    markdown = html_to_markdown(html)

    assert "## Financial statements" in markdown
    assert "## Risk management" in markdown
    assert "## Ordinary narrative text" not in markdown
    assert "## Revenue EUR millions" not in markdown
    assert "## Table label" not in markdown
    assert markdown.count("Financial statements") == 1
    assert markdown.count("Risk management") == 1


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
