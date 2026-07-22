"""HTML and XHTML to Markdown conversion for regulatory announcements."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from openfilings.exceptions import ExtractionError

_BLANK_LINES = re.compile(r"\n{3,}")
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_MAX_EXISTING_HEADINGS_WITH_RECOVERY = 20
_MAX_STYLED_HEADING_CANDIDATES = 300
_MAX_STYLED_HEADING_PX = 150.0
_MIN_STYLED_HEADING_PX = 90.0
_MIN_NAVIGATION_REFERENCES = 3
_FONT_SIZE_RULE = re.compile(
    r"\.([A-Za-z][\w-]*)\s*\{[^{}]*font-size\s*:\s*([0-9.]+)\s*(px|pt)",
    re.IGNORECASE,
)


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
            ["script", "noscript", "svg", "img", "link", "meta"]
        ):
            element.decompose()
        for element in soup.find_all(_is_non_display_ix_element):
            element.decompose()
        _recover_navigation_headings(soup)
        _recover_styled_headings(soup)
        for element in soup.find_all("style"):
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


def _recover_navigation_headings(soup: Any) -> None:
    if _has_sufficient_headings(soup):
        return

    labels_by_target: dict[str, Counter[str]] = defaultdict(Counter)
    for anchor in soup.find_all("a", href=True):
        target_id = _internal_target_id(anchor.get("href"))
        label = _navigation_label(anchor)
        if target_id and _is_heading_label(label):
            labels_by_target[target_id][label] += 1

    for target_id, labels in labels_by_target.items():
        label, references = labels.most_common(1)[0]
        if references < _MIN_NAVIGATION_REFERENCES:
            continue
        target = soup.find(id=target_id)
        if target is None or target.name in _HEADING_TAGS:
            continue
        heading = soup.new_tag("h2")
        heading.string = label
        target.insert_before(heading)


def _recover_styled_headings(soup: Any) -> None:
    if _has_sufficient_headings(soup):
        return
    font_sizes = _font_size_classes(soup)
    candidates = [
        candidate
        for element in soup.find_all("div", class_="t")
        if (candidate := _styled_heading_candidate(element, font_sizes))
    ]
    if not candidates or len(candidates) > _MAX_STYLED_HEADING_CANDIDATES:
        return

    seen: set[str] = set()
    for group in _consecutive_candidate_groups(candidates):
        label = _styled_heading_group_label(group)
        key = label.casefold()
        if not _is_styled_heading_label(label) or key in seen:
            continue
        seen.add(key)
        heading = soup.new_tag("h2")
        heading.string = label
        group[0][0].insert_before(heading)
        for element, *_ in group:
            element.decompose()


def _styled_heading_group_label(
    group: list[tuple[Any, str, float, object]],
) -> str:
    parts: list[str] = []
    for _, label, *_ in group:
        if not parts or label.casefold() != parts[-1].casefold():
            parts.append(label)
    return _collapse_repeated_phrase(_normalized_text(" ".join(parts)))


def _collapse_repeated_phrase(label: str) -> str:
    words = label.split()
    midpoint = len(words) // 2
    if len(words) % 2 == 0 and [word.casefold() for word in words[:midpoint]] == [
        word.casefold() for word in words[midpoint:]
    ]:
        return " ".join(words[:midpoint])
    return label


def _styled_heading_candidate(
    element: Any, font_sizes: dict[str, float]
) -> tuple[Any, str, float, object] | None:
    if element.find_parent(["table", "th", "td"]) is not None:
        return None
    size = max(
        (font_sizes.get(name, 0.0) for name in element.get("class", [])),
        default=0.0,
    )
    label = _normalized_text(element.get_text(" ", strip=True))
    if not _MIN_STYLED_HEADING_PX <= size <= _MAX_STYLED_HEADING_PX:
        return None
    if not _is_styled_heading_label(label):
        return None
    return element, label, size, element.find_parent(class_="pf")


def _consecutive_candidate_groups(
    candidates: list[tuple[Any, str, float, object]],
) -> list[list[tuple[Any, str, float, object]]]:
    groups: list[list[tuple[Any, str, float, object]]] = []
    current: list[tuple[Any, str, float, object]] = []
    previous_element: Any = None
    for candidate in candidates:
        element, _, size, page = candidate
        consecutive = (
            previous_element is not None
            and previous_element.find_next("div", class_="t") is element
        )
        same_style = (
            current and abs(current[-1][2] - size) <= 2 and current[-1][3] is page
        )
        if current and not (consecutive and same_style):
            groups.append(current)
            current = []
        current.append(candidate)
        previous_element = element
    if current:
        groups.append(current)
    return groups


def _font_size_classes(soup: Any) -> dict[str, float]:
    sizes: dict[str, float] = {}
    for style in soup.find_all("style"):
        for class_name, raw_size, unit in _FONT_SIZE_RULE.findall(style.get_text()):
            size = float(raw_size)
            sizes[class_name] = size * 4 / 3 if unit.casefold() == "pt" else size
    return sizes


def _has_sufficient_headings(soup: Any) -> bool:
    heading_limit = _MAX_EXISTING_HEADINGS_WITH_RECOVERY + 1
    return len(soup.find_all(_HEADING_TAGS, limit=heading_limit)) >= heading_limit


def _internal_target_id(href: object) -> str | None:
    value = str(href or "").strip()
    return value[1:] if value.startswith("#") and len(value) > 1 else None


def _navigation_label(anchor: Any) -> str:
    own_text = _normalized_text(anchor.get_text(" ", strip=True))
    container = anchor.find_parent(["td", "li"])
    if container is None:
        return own_text
    container_text = _normalized_text(container.get_text("", strip=False))
    return container_text if len(container_text) <= 120 else own_text


def _is_heading_label(label: str) -> bool:
    lowered = label.casefold()
    letters = sum(character.isalpha() for character in label)
    return (
        3 <= len(label) <= 120
        and letters >= 3
        and not lowered.startswith(("read more", "more in", "see "))
    )


def _is_styled_heading_label(label: str) -> bool:
    return _is_heading_label(label) and re.match(r"^[\d€$£]", label) is None


def _normalized_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())
