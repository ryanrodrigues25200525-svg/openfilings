"""Ergonomic domain objects inspired by EdgarTools' collection-first API."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date

from openfilings.models import Filing, FilingContent

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class Filings(Sequence[Filing]):
    """Immutable filing collection with filtering and latest/head helpers."""

    def __init__(self, filings: Sequence[Filing] = ()) -> None:
        self._filings = tuple(filings)

    def __len__(self) -> int:
        return len(self._filings)

    def __iter__(self) -> Iterator[Filing]:
        return iter(self._filings)

    def __getitem__(self, index: int | slice) -> Filing | Filings:
        value = self._filings[index]
        return Filings(value) if isinstance(index, slice) else value

    @property
    def empty(self) -> bool:
        return not self._filings

    def head(self, count: int = 5) -> Filings:
        if count < 0:
            raise ValueError("count cannot be negative")
        return Filings(self._filings[:count])

    def latest(self, count: int = 1) -> Filing | Filings | None:
        if count < 1:
            raise ValueError("count must be at least one")
        ordered = sorted(
            self._filings,
            key=lambda filing: (filing.published_at or filing.filing_date, filing.id),
            reverse=True,
        )
        selected = Filings(ordered[:count])
        if count == 1:
            return selected[0] if selected else None
        return selected

    def filter(
        self,
        *,
        source: str | None = None,
        filing_type: str | Sequence[str] | None = None,
        category: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        has_document: bool | None = None,
    ) -> Filings:
        types = (
            {filing_type.casefold()}
            if isinstance(filing_type, str)
            else {value.casefold() for value in filing_type or ()}
        )
        return Filings(
            filing
            for filing in self._filings
            if (source is None or filing.source == source)
            and (not types or filing.filing_type.casefold() in types)
            and (category is None or filing.category.casefold() == category.casefold())
            and (date_from is None or filing.filing_date >= date_from)
            and (date_to is None or filing.filing_date <= date_to)
            and (has_document is None or filing.has_document is has_document)
        )


@dataclass(frozen=True, slots=True)
class DocumentSection:
    """One Markdown section from a parsed filing document."""

    title: str
    level: int
    markdown: str
    start_line: int

    @property
    def text(self) -> str:
        return _HEADING.sub(r"\2", self.markdown, count=1).strip()


@dataclass(frozen=True, slots=True)
class FilingDocument:
    """A filing's Markdown plus section navigation and local search."""

    content: FilingContent
    sections: tuple[DocumentSection, ...]

    @classmethod
    def from_content(cls, content: FilingContent) -> FilingDocument:
        lines = content.markdown.splitlines()
        headings: list[tuple[int, int, str]] = []
        for line_number, line in enumerate(lines):
            match = _HEADING.match(line)
            if match:
                headings.append((line_number, len(match.group(1)), match.group(2)))

        sections: list[DocumentSection] = []
        for position, (start, level, title) in enumerate(headings):
            end = len(lines)
            for candidate_start, candidate_level, _ in headings[position + 1 :]:
                if candidate_level <= level:
                    end = candidate_start
                    break
            sections.append(
                DocumentSection(
                    title=title,
                    level=level,
                    markdown="\n".join(lines[start:end]).strip() + "\n",
                    start_line=start + 1,
                )
            )
        return cls(content=content, sections=tuple(sections))

    @property
    def markdown(self) -> str:
        return self.content.markdown

    def section(self, title: str) -> DocumentSection | None:
        wanted = title.casefold()
        return next(
            (
                section
                for section in self.sections
                if wanted in section.title.casefold()
            ),
            None,
        )

    def search(self, query: str, *, limit: int = 20) -> tuple[DocumentSection, ...]:
        wanted = query.strip().casefold()
        if not wanted or limit < 1:
            return ()
        return tuple(
            section
            for section in self.sections
            if wanted in section.markdown.casefold()
        )[:limit]
