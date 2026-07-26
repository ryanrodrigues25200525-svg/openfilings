from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from openfilings.insider import extract_nsm_insider_dealings
from openfilings.models import Filing


def test_extract_nsm_insider_dealings_parses_real_mar_form() -> None:
    filing = _filing()
    html = _fixture("fca_nsm_pdmar_dealing.html")

    dealings = extract_nsm_insider_dealings(html, filing)

    assert len(dealings) == 1
    dealing = dealings[0]
    assert dealing.filing_id == "uk_nsm_pdmar"
    assert dealing.person_name == "Nikki Grady-Smith"
    assert dealing.position == "Chief Transformation Officer"
    assert dealing.initial_or_amendment == "Initial notification"
    assert dealing.instrument == "Ordinary Shares of 20p each GB00B63H8491"
    assert dealing.isin == "GB00B63H8491"
    assert dealing.transaction_natures == (
        "Sale of Shares to cover statutory withholding liabilities",
    )
    assert len(dealing.price_volume) == 1
    assert dealing.price_volume[0].price == Decimal("11.49591")
    assert dealing.price_volume[0].currency == "GBP"
    assert dealing.price_volume[0].volume == 31169
    assert dealing.transaction_dates == (date(2026, 3, 23),)
    assert dealing.places == ("London Stock Exchange, Main Market (XLON)",)


def test_extract_nsm_insider_dealings_parses_real_eqs_wire_form() -> None:
    """EQS-distributed filings prepend a single cell containing the whole
    announcement as one prose paragraph - itself embedding a copy of every
    field label - ahead of the real structured rows, in the same table as
    the structured rows. Confirmed live on a real Fuller, Smith & Turner
    PLC filing, where that prose blob's embedded "Reason for the
    notification" text was matched first, resolving every section index
    to 0 and silently discarding the filing."""

    filing = Filing(
        id="uk_nsm_fuller",
        company_id="uk_lei_213800C7ACOFMRCQQW76",
        source="fca_nsm",
        source_id="fuller",
        title="Director/PDMR Shareholding",
        category="Director/PDMR Shareholding",
        filing_type="DSH",
        filing_date=date(2026, 7, 24),
        issuer_name="Fuller, Smith & Turner P.L.C.",
        source_url="https://data.fca.org.uk/artefacts/NSM/EQS/fuller.html",
    )
    html = _fixture("fca_nsm_pdmr_eqs_dealing.html")

    dealings = extract_nsm_insider_dealings(html, filing)

    assert len(dealings) == 1
    dealing = dealings[0]
    assert dealing.person_name == "Peter Turner"
    assert dealing.position == "Property Director"
    assert dealing.initial_or_amendment == "Initial Notification"
    assert dealing.isin == "GB00B1YPC344"
    assert dealing.transaction_natures == (
        "Transfer of shares from the Estate of Catherine Mary Turner, deceased.",
    )
    assert dealing.transaction_dates == (date(2026, 7, 23),)
    assert dealing.places == ("Outside of trading venue",)


def test_extract_nsm_insider_dealings_rejects_unstructured_narrative() -> None:
    dealings = extract_nsm_insider_dealings(
        "<html><body><p>A director bought 100 shares.</p></body></html>",
        _filing(),
    )

    assert dealings == []


def _filing() -> Filing:
    return Filing(
        id="uk_nsm_pdmar",
        company_id="uk_lei_213800EC7997ZBLZJH69",
        source="fca_nsm",
        source_id="pdmar",
        title="Director/PDMR Shareholding",
        category="Director/PDMR Shareholding",
        filing_type="DSH",
        filing_date=date(2026, 3, 24),
        issuer_name="Rolls-Royce Holdings plc",
        source_url="https://data.fca.org.uk/artefacts/NSM/RNS/pdmar.html",
    )


def _fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")
