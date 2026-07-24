"""Structured extraction for UK "TR-1: Standard form for notification of
major holdings" disclosures (NSM type code ``HOL``).

FCA prescribes a fixed section order for this form (Issuer Details, Reason
for Notification, Details of person subject to the notification obligation,
...), so the same label sequence holds across filers even though the
underlying HTML is Word-exported and varies filer to filer. This parses
that sequence rather than guessing at semantic markup, and returns None
- not a wrong answer - when the expected labels aren't found in order, so
callers can fall back to the raw filing.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from openfilings.models import Filing, MajorHolderNotification

_PERSON_SECTION_LABEL = "3. Details of person subject to the notification obligation"
_TOTALS_ROW_LABEL = (
    "Resulting situation on the date on which threshold was crossed or reached"
)
_TOTAL_PERCENT_COLUMN = "Total of both in % (8.A + 8.B)"
_TOTAL_VOTING_RIGHTS_COLUMN = "Total number of voting rights held in issuer"


def extract_nsm_major_holder(
    html: str, filing: Filing
) -> MajorHolderNotification | None:
    soup = BeautifulSoup(html, "html.parser")
    tokens = [
        token
        for token in (t.strip() for t in soup.get_text(separator="|").split("|"))
        if token
    ]

    person_section = _find(tokens, _PERSON_SECTION_LABEL)
    if person_section is None:
        return None
    holder_name = _value_after(tokens, "Name", start=person_section)
    if not holder_name:
        return None

    isin = _value_after(tokens, "ISIN")
    reason = _value_after(tokens, "2. Reason for Notification")
    date_crossed = _parse_date(
        _value_after(tokens, "5. Date on which the threshold was crossed or reached")
    )
    date_notified = _parse_date(
        _value_after(tokens, "6. Date on which Issuer notified")
    )
    total_percent, total_voting_rights = _totals_from_table(soup)

    return MajorHolderNotification(
        filing_id=filing.id,
        company_id=filing.company_id,
        issuer_name=filing.issuer_name or "Unknown issuer",
        holder_name=holder_name,
        isin=isin,
        reason=reason,
        date_crossed=date_crossed,
        date_notified=date_notified,
        total_percent=total_percent,
        total_voting_rights=total_voting_rights,
        source_url=filing.source_url,
    )


def _totals_from_table(soup: BeautifulSoup) -> tuple[Decimal | None, int | None]:
    for table in soup.find_all("table"):
        header_row = None
        totals_row = None
        for row in table.find_all("tr"):
            cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
            if not cells:
                continue
            if _TOTAL_PERCENT_COLUMN in cells:
                header_row = cells
            elif cells[0] == _TOTALS_ROW_LABEL:
                totals_row = cells
        if header_row is None or totals_row is None:
            continue
        percent = _column_value(header_row, totals_row, _TOTAL_PERCENT_COLUMN)
        voting_rights = _column_value(
            header_row, totals_row, _TOTAL_VOTING_RIGHTS_COLUMN
        )
        return _parse_decimal(percent), _parse_int(voting_rights)
    return None, None


def _column_value(
    header_row: list[str], data_row: list[str], column_label: str
) -> str | None:
    try:
        index = header_row.index(column_label)
    except ValueError:
        return None
    return data_row[index] if index < len(data_row) else None


def _find(tokens: list[str], label: str, *, start: int = 0) -> int | None:
    for index in range(start, len(tokens)):
        if tokens[index] == label:
            return index
    return None


def _value_after(tokens: list[str], label: str, *, start: int = 0) -> str | None:
    index = _find(tokens, label, start=start)
    return tokens[index + 1] if index is not None and index + 1 < len(tokens) else None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.replace(",", ""))
    except ValueError:
        return None
