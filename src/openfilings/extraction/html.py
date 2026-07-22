"""HTML and XHTML to Markdown conversion for regulatory announcements."""

from __future__ import annotations

import re

from openfilings.exceptions import ExtractionError

_BLANK_LINES = re.compile(r"\n{3,}")


def html_to_markdown(html_bytes: bytes) -> str:
    """Convert filing HTML to Markdown while removing scripts and tracking images."""

    if not html_bytes.strip():
        raise ExtractionError("The source HTML document is empty.")

    try:
        # Keep HTML dependencies off the ordinary CLI startup path.
        from bs4 import BeautifulSoup
        from markdownify import markdownify

        soup = BeautifulSoup(html_bytes, "html.parser")
        for element in soup.find_all(
            ["script", "style", "noscript", "svg", "img", "link", "meta"]
        ):
            element.decompose()
        for element in soup.find_all(_is_non_display_ix_element):
            element.decompose()
        root = soup.body or soup
        markdown = markdownify(
            str(root),
            heading_style="ATX",
            bullets="-",
        )
    except Exception as exc:
        raise ExtractionError(f"HTML-to-Markdown conversion failed: {exc}") from exc

    normalized = markdown.replace("\xa0", " ").strip()
    normalized = _BLANK_LINES.sub("\n\n", normalized)
    if not normalized:
        raise ExtractionError("The HTML document contained no extractable text.")
    return normalized + "\n"


def _is_non_display_ix_element(element: object) -> bool:
    name = str(getattr(element, "name", "") or "").casefold()
    if ":" not in name:
        return False
    prefix, local = name.rsplit(":", 1)
    return prefix == "ix" and local in {"header", "hidden", "exclude"}
