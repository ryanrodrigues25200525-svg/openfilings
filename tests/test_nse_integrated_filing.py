from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from openfilings.adapters.nse import NseClient
from openfilings.models import Filing
from openfilings.xbrl.extract import extract_nse_integrated_filing_financials
from openfilings.xbrl.nse_xbrl_parser import parse_nse_xbrl_instance

_NAMESPACES = (
    'xmlns:xbrli="http://www.xbrl.org/2003/instance" '
    'xmlns:in-capmkt="http://www.sebi.gov.in/xbrl/2026-01-31/in-capmkt" '
    'xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
    'xmlns:xbrldi="http://xbrl.org/2006/xbrldi"'
)


def _instance(*, include_attributable: bool = True) -> bytes:
    attributable = (
        '<in-capmkt:EquityAttributableToOwnersOfParent contextRef="OneI" '
        'decimals="-3" unitRef="INR">900</in-capmkt:EquityAttributableToOwnersOfParent>'
        if include_attributable
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl {_NAMESPACES}>
  <xbrli:context id="OneI">
    <xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>
  </xbrli:context>
  <xbrli:context id="FourD">
    <xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>
    <xbrli:period>
      <xbrli:startDate>2025-04-01</xbrli:startDate>
      <xbrli:endDate>2026-03-31</xbrli:endDate>
    </xbrli:period>
  </xbrli:context>
  <xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
  <in-capmkt:Assets contextRef="OneI" decimals="-3" unitRef="INR">
    1000</in-capmkt:Assets>
  <in-capmkt:Liabilities contextRef="OneI" decimals="-3" unitRef="INR">
    100</in-capmkt:Liabilities>
  <in-capmkt:Equity contextRef="OneI" decimals="-3" unitRef="INR">900</in-capmkt:Equity>
  {attributable}
  <in-capmkt:Revenue contextRef="FourD" decimals="-3" unitRef="INR">
    500</in-capmkt:Revenue>
  <in-capmkt:ProfitLoss contextRef="FourD" decimals="-3" unitRef="INR">
    50</in-capmkt:ProfitLoss>
</xbrli:xbrl>""".encode()


def _filing() -> Filing:
    return Filing(
        id="in_nse_filing_1",
        company_id="in_nse_RELIANCE",
        source="nse",
        source_id="1",
        title="Annual Report",
        category="accounts",
        filing_type="annual",
        filing_date=date(2026, 5, 1),
        period_end=date(2026, 3, 31),
        source_url="https://nsearchives.nseindia.com/corporate/xbrl/1.xml",
    )


def test_parse_nse_xbrl_instance_reads_contexts_units_and_facts() -> None:
    parsed = parse_nse_xbrl_instance(_instance())

    assert parsed.contexts["OneI"].instant == date(2026, 3, 31)
    assert parsed.contexts["FourD"].start_date == date(2025, 4, 1)
    assert parsed.contexts["FourD"].end_date == date(2026, 3, 31)
    assert parsed.units["INR"] == "INR"
    assert any(
        fact.concept == "Assets" and fact.value == Decimal("1000")
        for fact in parsed.facts
    )


def test_nse_integrated_filing_extraction_builds_normalized_statements() -> None:
    financials = extract_nse_integrated_filing_financials(
        _instance(),
        _filing(),
        source_url="https://nsearchives.nseindia.com/corporate/xbrl/1.xml",
        sha256="a" * 64,
    )

    assert financials.extraction_method == "nse-integrated-filing-xbrl"
    balance = financials.balance_sheet()
    assert balance is not None
    by_code = {item.code: item.values[0].value for item in balance.line_items}
    assert by_code["total_assets"] == Decimal("1000")
    assert by_code["total_liabilities"] == Decimal("100")
    assert by_code["total_equity"] == Decimal("900")
    total = by_code["total_liabilities"] + by_code["total_equity"]
    assert total == by_code["total_assets"]

    income = financials.income_statement()
    assert income is not None
    net_income = next(
        item for item in income.line_items if item.code == "net_income_loss"
    )
    assert net_income.values[0].value == Decimal("50")


def test_equity_alias_prefers_the_full_total_over_a_parent_only_component() -> None:
    """A filer can tag both "Equity" (the full total, including any
    non-controlling interests) and "EquityAttributableToOwnersOfParent" (a
    subset) with identical period/context coverage - the alias list's own
    order (Equity listed first) must break the tie, not fact-count or
    insertion order, or the balance-sheet identity silently breaks."""
    financials = extract_nse_integrated_filing_financials(
        _instance(include_attributable=True),
        _filing(),
        source_url="https://example.test/1.xml",
        sha256="b" * 64,
    )

    balance = financials.balance_sheet()
    assert balance is not None
    total_equity = next(
        item for item in balance.line_items if item.code == "total_equity"
    )
    assert total_equity.concept == "Equity"
    assert total_equity.values[0].value == Decimal("900")


def _integrated_filing_row(**overrides: object) -> dict[str, object]:
    row = {
        "symbol": "RELIANCE",
        "qe_Date": "31-MAR-2026",
        "type_Sub": "Original",
        "audited": "Audited",
        "consolidated": "Consolidated",
        "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/1.xml",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_integrated_filing_xbrl_prefers_consolidated_audited_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/integrated-filing-results":
            return httpx.Response(
                200,
                json={
                    "data": [
                        _integrated_filing_row(
                            consolidated="Standalone",
                            xbrl=(
                                "https://nsearchives.nseindia.com/corporate/"
                                "xbrl/standalone.xml"
                            ),
                        ),
                        _integrated_filing_row(),
                        _integrated_filing_row(qe_Date="30-JUN-2025"),
                    ]
                },
            )
        if request.url.path == "/corporate/xbrl/1.xml":
            return httpx.Response(200, content=_instance())
        return httpx.Response(200, content=b"")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = NseClient(client=http)
        result = await client.integrated_filing_xbrl("RELIANCE", date(2026, 3, 31))

    assert result is not None
    data, source_url = result
    assert source_url == "https://nsearchives.nseindia.com/corporate/xbrl/1.xml"
    assert data == _instance()


@pytest.mark.asyncio
async def test_integrated_filing_xbrl_returns_none_without_a_matching_period() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [_integrated_filing_row(qe_Date="30-JUN-2025")]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = NseClient(client=http)
        result = await client.integrated_filing_xbrl("RELIANCE", date(2026, 3, 31))

    assert result is None
