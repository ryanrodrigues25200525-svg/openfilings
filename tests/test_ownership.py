from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from openfilings.models import Filing
from openfilings.ownership import extract_nsm_major_holder


def test_extract_nsm_major_holder_parses_real_tr1_document() -> None:
    html = _fixture("fca_nsm_tr1_holding.html")
    filing = Filing(
        id="uk_nsm_test",
        company_id="uk_lei_213800TSKOLX4EU6L377",
        source="fca_nsm",
        source_id="test",
        title="Holding(s) in Company",
        category="major_holdings",
        filing_type="HOL",
        filing_date=date(2026, 7, 24),
        issuer_name="BABCOCK INTERNATIONAL GROUP PLC",
        source_url="https://data.fca.org.uk/artefacts/NSM/RNS/test.html",
    )

    holder = extract_nsm_major_holder(html, filing)

    assert holder is not None
    assert holder.filing_id == "uk_nsm_test"
    assert holder.issuer_name == "BABCOCK INTERNATIONAL GROUP PLC"
    assert holder.holder_name == (
        "Boston Partners FKA Robeco Investment Management, Inc."
    )
    assert holder.isin == "GB0009697037"
    assert holder.reason == "An acquisition or disposal of voting rights"
    assert holder.date_crossed == date(2026, 7, 22)
    assert holder.date_notified == date(2026, 7, 23)
    assert holder.total_percent == Decimal("3.010000")
    assert holder.total_voting_rights == 14798922


def test_extract_nsm_major_holder_returns_none_for_unrecognized_document() -> None:
    filing = Filing(
        id="uk_nsm_other",
        company_id="uk_lei_x",
        source="fca_nsm",
        source_id="other",
        title="Trading update",
        category="disclosure",
        filing_type="UPD",
        filing_date=date(2026, 1, 1),
        issuer_name="Example plc",
        source_url="https://example.test",
    )

    result = extract_nsm_major_holder(
        "<html><body><p>Nothing structured here.</p></body></html>", filing
    )

    assert result is None


def _fixture(name: str) -> str:
    path = Path(__file__).parent / "fixtures" / name
    return path.read_text(encoding="utf-8")
