"""Structured extraction for UK MAR Article 19 PDMR/PCA notifications."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, Tag

from openfilings.models import Filing, InsiderDealing, InsiderPriceVolume

_PERSON_SECTION = "discharging managerial responsibilities"
_INSTRUMENT_LABEL = "description of the financial instrument"
_ISIN_PATTERN = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}[0-9]\b")


def extract_nsm_insider_dealings(html: str, filing: Filing) -> list[InsiderDealing]:
    """Parse the standard UK MAR notification tables in one DSH filing.

    The section-4 heading ("Details of the transaction(s)...") is anchored
    on the financial-instrument label instead of its own header text -
    live filings have been observed with that header duplicated/misworded
    (e.g. a real CMC Markets DSH filing whose item 4 heading literally
    repeats item 3's "Details of the issuer..." text), while the
    instrument label itself has been reliable across every live filing
    checked."""

    soup = BeautifulSoup(html, "html.parser")
    dealings: list[InsiderDealing] = []
    tables = [
        table for table in soup.find_all("table") if table.find_parent("table") is None
    ]
    for table in tables:
        tokens = _tokens(table)
        normalized = [_normalize(token) for token in tokens]
        if (
            not any(_PERSON_SECTION in token for token in normalized)
            or _find_contains(normalized, _INSTRUMENT_LABEL) is None
        ):
            continue
        reason_index = _find_contains(normalized, "reason for the notification")
        transaction_index = _find_contains(normalized, _INSTRUMENT_LABEL)
        if reason_index is None or transaction_index is None:
            continue
        person_name = _value_after(
            tokens, normalized, "name", start=0, end=reason_index
        )
        if not person_name:
            continue
        instrument = _value_after(
            tokens,
            normalized,
            "description of the financial instrument",
            start=transaction_index,
        )
        isin_match = _ISIN_PATTERN.search(" ".join(tokens[transaction_index:]))
        dealings.append(
            InsiderDealing(
                filing_id=filing.id,
                company_id=filing.company_id,
                issuer_name=filing.issuer_name or "Unknown issuer",
                person_name=person_name,
                position=_value_after(
                    tokens,
                    normalized,
                    "position/status",
                    start=reason_index,
                    end=transaction_index,
                ),
                initial_or_amendment=_value_after(
                    tokens,
                    normalized,
                    "initial notification",
                    start=reason_index,
                    end=transaction_index,
                ),
                instrument=instrument,
                isin=isin_match.group(0) if isin_match else None,
                transaction_natures=tuple(
                    _values_after(
                        tokens,
                        normalized,
                        "nature of the transaction",
                        start=transaction_index,
                    )
                ),
                price_volume=tuple(_price_volume(table)),
                transaction_dates=tuple(
                    parsed
                    for value in _values_after(
                        tokens,
                        normalized,
                        "date of the transaction",
                        start=transaction_index,
                    )
                    if (parsed := _parse_date(value)) is not None
                ),
                places=tuple(
                    _values_after(
                        tokens,
                        normalized,
                        "place of the transaction",
                        start=transaction_index,
                    )
                ),
                source_url=filing.source_url,
            )
        )
    return dealings


def _tokens(table: Tag) -> list[str]:
    """One token per table cell, in document order.

    Real FCA NSM filings are Word-exported HTML that sometimes splits a
    single word across adjacent ``<span>`` elements (e.g. "Details of" +
    " " + "p" + "erson"). Cell.get_text() with no separator concatenates
    those raw text nodes exactly as authored - including the genuine
    space between words and the absence of one mid-word - so collapsing
    runs of whitespace afterwards reconstructs the original text without
    corrupting it the way a per-node-stripped join would.

    Some wire services (observed live from EQS) prepend a single cell
    containing the entire announcement as one plain-text paragraph -
    itself embedding a copy of every field label - ahead of the actual
    structured rows, in the same outer table. Left in, that cell's copy
    of "Reason for the notification" etc. is the first match, resolving
    every section index to 0 and breaking extraction. No genuine field
    label or value in this form runs anywhere near this long, so cells
    past the cap are dropped as prose, not structured data."""

    _MAX_CELL_CHARS = 600
    tokens: list[str] = []
    for row in table.find_all("tr"):
        for cell in row.find_all(["td", "th"], recursive=False):
            text = re.sub(r"\s+", " ", cell.get_text()).strip()
            if text and len(text) <= _MAX_CELL_CHARS:
                tokens.append(text)
    return tokens


def _normalize(value: str) -> str:
    """Casefold, strip punctuation, and drop the article "the".

    Live filings from different filing agents word the same standard-form
    label inconsistently around "the" - "Description of the financial
    instrument" vs. "Description of financial instrument", "Date of the
    transaction" vs. "Date of transaction". Since both label lookups and
    the haystack go through this function, dropping "the" symmetrically
    makes matching agent-agnostic instead of chasing each wording."""

    words = re.sub(r"[^a-z0-9]+", " ", value.casefold()).split()
    return " ".join(word for word in words if word != "the")


def _find_contains(
    normalized: list[str],
    label: str,
    *,
    start: int = 0,
    end: int | None = None,
) -> int | None:
    target = _normalize(label)
    stop = len(normalized) if end is None else min(end, len(normalized))
    return next(
        (index for index in range(start, stop) if target in normalized[index]),
        None,
    )


def _value_after(
    tokens: list[str],
    normalized: list[str],
    label: str,
    *,
    start: int = 0,
    end: int | None = None,
) -> str | None:
    index = _find_contains(normalized, label, start=start, end=end)
    stop = len(tokens) if end is None else min(end, len(tokens))
    return tokens[index + 1] if index is not None and index + 1 < stop else None


def _values_after(
    tokens: list[str],
    normalized: list[str],
    label: str,
    *,
    start: int = 0,
) -> list[str]:
    target = _normalize(label)
    return [
        tokens[index + 1]
        for index in range(start, len(tokens) - 1)
        if target in normalized[index]
    ]


def _price_volume(table: Tag) -> list[InsiderPriceVolume]:
    lots: list[InsiderPriceVolume] = []
    seen: set[tuple[Decimal | None, str | None, int | None]] = set()
    for candidate in [table, *table.find_all("table")]:
        rows = candidate.find_all("tr")
        for index, row in enumerate(rows):
            cells = [
                cell.get_text(" ", strip=True)
                for cell in row.find_all(["td", "th"], recursive=False)
            ]
            normalized = [_normalize(cell) for cell in cells]
            price_index = next(
                (
                    cell_index
                    for cell_index, value in enumerate(normalized)
                    if value in {"price", "price s"}
                ),
                None,
            )
            volume_index = next(
                (
                    cell_index
                    for cell_index, value in enumerate(normalized)
                    if value in {"volume", "volume s"}
                ),
                None,
            )
            if price_index is None or volume_index is None:
                continue
            for data_row in rows[index + 1 :]:
                values = [
                    cell.get_text(" ", strip=True)
                    for cell in data_row.find_all(["td", "th"], recursive=False)
                ]
                if max(price_index, volume_index) >= len(values):
                    break
                price, currency = _parse_price(values[price_index])
                volume = _parse_int(values[volume_index])
                if price is None and volume is None:
                    break
                key = (price, currency, volume)
                if key not in seen:
                    seen.add(key)
                    lots.append(
                        InsiderPriceVolume(
                            price=price,
                            currency=currency,
                            volume=volume,
                        )
                    )
    return lots


def _parse_price(value: str) -> tuple[Decimal | None, str | None]:
    clean_value = value.strip()
    if not clean_value or clean_value.casefold() in {"nil", "n/a"}:
        return None, None
    currency = None
    if "£" in clean_value:
        currency = "GBP"
    elif "€" in clean_value:
        currency = "EUR"
    elif "$" in clean_value:
        currency = "USD"
    elif re.search(r"\bp(?:ence)?\b", clean_value, re.I):
        currency = "GBX"
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", clean_value)
    if match is None:
        return None, currency
    try:
        return Decimal(match.group(0).replace(",", "")), currency
    except InvalidOperation:
        return None, currency


def _parse_int(value: str) -> int | None:
    match = re.search(r"\d[\d,]*", value)
    try:
        return int(match.group(0).replace(",", "")) if match else None
    except ValueError:
        return None


def _parse_date(value: str) -> date | None:
    clean_value = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value, flags=re.I)
    for fmt in ("%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(clean_value.strip(), fmt).date()
        except ValueError:
            continue
    return None
