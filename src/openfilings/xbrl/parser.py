"""Bounded streaming parser for the Inline XBRL facts needed by OpenFilings."""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

from openfilings.exceptions import FinancialsUnavailableError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES

_WHITESPACE = re.compile(r"\s+")
_NUMERIC_TAGS = {"nonfraction", "fraction"}
_FACT_TAGS = _NUMERIC_TAGS | {"nonnumeric"}
_MAX_FACTS = 100_000
_MAX_CONTEXTS = 20_000
_FEED_CHUNK_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class XbrlContext:
    id: str
    start_date: date | None
    end_date: date | None
    instant: date | None
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class XbrlFact:
    concept: str
    context_ref: str
    unit_ref: str | None
    decimals: str | None
    value: Decimal | None
    raw_value: str
    numeric: bool


@dataclass(frozen=True, slots=True)
class ParsedXbrl:
    contexts: dict[str, XbrlContext]
    units: dict[str, str]
    facts: tuple[XbrlFact, ...]
    taxonomy_namespaces: tuple[str, ...]


@dataclass(slots=True)
class _ContextBuilder:
    id: str
    start_date: date | None = None
    end_date: date | None = None
    instant: date | None = None
    dimensions: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class _FactBuilder:
    tag: str
    attrs: dict[str, str]
    text: list[str] = field(default_factory=list)


class InlineXbrlParser(HTMLParser):
    """Extract contexts, units, and inline facts without building a large DOM."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contexts: dict[str, XbrlContext] = {}
        self.units: dict[str, str] = {}
        self.facts: list[XbrlFact] = []
        self.continuations: dict[str, str] = {}
        self.taxonomy_prefixes: set[str] = set()
        self._context: _ContextBuilder | None = None
        self._context_capture: tuple[str, str | None, list[str]] | None = None
        self._unit_id: str | None = None
        self._unit_parts: list[str] = []
        self._capture_measure = False
        self._fact_stack: list[_FactBuilder] = []
        self._continuation_id: str | None = None
        self._continuation_text: list[str] = []
        self._exclude_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        local = _local_name(tag)
        attributes = {key.casefold(): value or "" for key, value in attrs}

        if local == "context":
            context_id = attributes.get("id", "").strip()
            if context_id:
                if len(self.contexts) >= _MAX_CONTEXTS:
                    raise FinancialsUnavailableError(
                        "Inline XBRL document contains too many contexts."
                    )
                self._context = _ContextBuilder(id=context_id)
            return
        if self._context is not None and local in {
            "startdate",
            "enddate",
            "instant",
            "explicitmember",
        }:
            self._context_capture = (
                local,
                attributes.get("dimension"),
                [],
            )
            return

        if local == "unit":
            self._unit_id = attributes.get("id", "").strip() or None
            self._unit_parts = []
            return
        if self._unit_id is not None and local == "measure":
            self._capture_measure = True
            return

        if local in _FACT_TAGS:
            if len(self.facts) + len(self._fact_stack) >= _MAX_FACTS:
                raise FinancialsUnavailableError(
                    "Inline XBRL document contains too many facts."
                )
            self._fact_stack.append(_FactBuilder(tag=local, attrs=attributes))
            return
        if local == "continuation":
            self._continuation_id = attributes.get("id", "").strip() or None
            self._continuation_text = []
            return
        if local == "exclude":
            self._exclude_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._context_capture is not None:
            self._context_capture[2].append(data)
        if self._capture_measure:
            self._unit_parts.append(data)
        if self._continuation_id and self._exclude_depth == 0:
            self._continuation_text.append(data)
        if self._fact_stack and self._exclude_depth == 0:
            self._fact_stack[-1].text.append(data)

    def handle_endtag(self, tag: str) -> None:
        local = _local_name(tag)
        if local == "exclude" and self._exclude_depth:
            self._exclude_depth -= 1
            return

        if self._context_capture is not None and local == self._context_capture[0]:
            self._finish_context_capture()
            return
        if local == "context" and self._context is not None:
            context = self._context
            self.contexts[context.id] = XbrlContext(
                id=context.id,
                start_date=context.start_date,
                end_date=context.end_date,
                instant=context.instant,
                dimensions=tuple(sorted(context.dimensions)),
            )
            self._context = None
            return

        if local == "measure" and self._capture_measure:
            self._capture_measure = False
            return
        if local == "unit" and self._unit_id is not None:
            value = " / ".join(
                part
                for part in (_clean_text(text) for text in self._unit_parts)
                if part
            )
            self.units[self._unit_id] = value
            self._unit_id = None
            self._unit_parts = []
            return

        if local in _FACT_TAGS and self._fact_stack:
            builder = self._fact_stack.pop()
            if builder.tag == local:
                self._finish_fact(builder)
            return
        if local == "continuation" and self._continuation_id:
            self.continuations[self._continuation_id] = _clean_text(
                "".join(self._continuation_text)
            )
            self._continuation_id = None
            self._continuation_text = []

    def result(self) -> ParsedXbrl:
        facts = tuple(self._with_continuation(fact) for fact in self.facts)
        return ParsedXbrl(
            contexts=self.contexts,
            units=self.units,
            facts=facts,
            taxonomy_namespaces=tuple(sorted(self.taxonomy_prefixes)),
        )

    def _finish_context_capture(self) -> None:
        if self._context is None or self._context_capture is None:
            return
        field_name, dimension, parts = self._context_capture
        value = _clean_text("".join(parts))
        if field_name == "explicitmember" and dimension and value:
            self._context.dimensions.append((dimension, value))
        elif field_name in {"startdate", "enddate", "instant"}:
            parsed = _parse_date(value)
            setattr(self._context, _snake_case(field_name), parsed)
        self._context_capture = None

    def _finish_fact(self, builder: _FactBuilder) -> None:
        concept = builder.attrs.get("name", "").strip()
        context_ref = builder.attrs.get("contextref", "").strip()
        if not concept or not context_ref:
            return
        if ":" in concept:
            self.taxonomy_prefixes.add(concept.split(":", 1)[0])
        raw_value = _clean_text("".join(builder.text))
        numeric = builder.tag in _NUMERIC_TAGS
        value = _parse_numeric(raw_value, builder.attrs) if numeric else None
        self.facts.append(
            XbrlFact(
                concept=concept,
                context_ref=context_ref,
                unit_ref=builder.attrs.get("unitref") or None,
                decimals=builder.attrs.get("decimals") or None,
                value=value,
                raw_value=raw_value,
                numeric=numeric,
            )
        )

    def _with_continuation(self, fact: XbrlFact) -> XbrlFact:
        # Numeric statement facts are never expected to use continuations. Keeping
        # the hook here makes narrative facts complete for future document APIs.
        return fact


def parse_inline_xbrl(document: bytes) -> ParsedXbrl:
    """Parse one XHTML report into a compact fact/context representation."""

    if not document.strip():
        raise FinancialsUnavailableError("The tagged report is empty.")
    if len(document) > MAX_TAGGED_DOCUMENT_BYTES:
        raise FinancialsUnavailableError(
            "The tagged report exceeds the "
            f"{MAX_TAGGED_DOCUMENT_BYTES // 1024 // 1024} MB limit."
        )
    parser = InlineXbrlParser()
    try:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        for offset in range(0, len(document), _FEED_CHUNK_BYTES):
            parser.feed(decoder.decode(document[offset : offset + _FEED_CHUNK_BYTES]))
        final_text = decoder.decode(b"", final=True)
        if final_text:
            parser.feed(final_text)
        parser.close()
    except FinancialsUnavailableError:
        raise
    except Exception as exc:
        raise FinancialsUnavailableError(f"Could not parse Inline XBRL: {exc}") from exc
    result = parser.result()
    if not any(fact.numeric and fact.value is not None for fact in result.facts):
        raise FinancialsUnavailableError(
            "The filing contains no usable Inline XBRL numeric facts."
        )
    return result


def _parse_numeric(raw_value: str, attrs: dict[str, str]) -> Decimal | None:
    if attrs.get("xsi:nil", "").casefold() == "true" or not raw_value:
        return None
    value = raw_value.replace("\u2212", "-").replace("\xa0", "").strip()
    transform = attrs.get("format", "").casefold()
    if not value or value in {"-", "\u2013", "\u2014"}:
        return Decimal(0) if "zero" in transform else None

    negative = value.startswith("(") and value.endswith(")")
    value = value.strip("()")
    value = re.sub(r"[^0-9,\.\-+]", "", value)
    if "comma-decimal" in transform or "commadecimal" in transform:
        value = value.replace(".", "").replace(",", ".")
    else:
        value = value.replace(",", "")
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    if negative:
        number = -abs(number)
    if attrs.get("sign") == "-":
        number = -abs(number)
    try:
        scale = int(attrs.get("scale", "0") or "0")
    except ValueError:
        scale = 0
    return number * (Decimal(10) ** scale)


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _clean_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value.replace("\xa0", " ")).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit(":", 1)[-1].casefold()


def _snake_case(value: str) -> str:
    return {"startdate": "start_date", "enddate": "end_date"}.get(value, value)
