from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from openfilings.adapters.base import SourceDocument
from openfilings.domain import FilingDocument, Filings
from openfilings.exceptions import FinancialsUnavailableError
from openfilings.models import ExtractionQuality, Filing, FilingContent
from openfilings.storage.sqlite import SQLiteCache
from openfilings.xbrl import extract_filing_financials


def test_inline_xbrl_builds_normalized_statements() -> None:
    financials = extract_filing_financials(_document(_ixbrl()), _filing())

    assert financials.fact_count == 11
    assert financials.taxonomy_namespaces == ("ifrs-full",)
    assert [item.statement_type for item in financials.statements] == [
        "income_statement",
        "balance_sheet",
        "cash_flow_statement",
    ]

    income = financials.statements[0]
    revenue = next(item for item in income.line_items if item.code == "revenue")
    assert income.currency == "GBP"
    assert revenue.concept == "ifrs-full:Revenue"
    assert [value.value for value in revenue.values] == [
        Decimal("1234000"),
        Decimal("1100000"),
    ]
    # The dimensional segment fact is deliberately excluded in favour of totals.
    assert all(not value.dimensions for value in revenue.values)

    cash_flow = financials.statements[2]
    investing = next(
        item for item in cash_flow.line_items if item.code == "investing_cash_flow"
    )
    assert investing.values[0].value == Decimal("-210000")


def test_eu_comma_decimal_transform_and_zip_package() -> None:
    report = _ixbrl().replace(
        b'decimals="2">\n          0.42',
        b'decimals="2" format="ixt:num-comma-decimal">\n          0,42',
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("reports/report.xhtml", report)
        package.writestr("reports/placeholder.html", "<html>x</html>")

    financials = extract_filing_financials(
        SourceDocument(
            data=archive.getvalue(),
            media_type="application/zip",
            source_url="https://example.test/report.zip",
        ),
        _filing(),
    )

    eps = next(
        item for item in financials.statements[0].line_items if item.code == "basic_eps"
    )
    assert eps.values[0].value == Decimal("0.42")


def test_uk_gaap_concept_aliases_are_normalized() -> None:
    report = (
        _ixbrl()
        .replace(b"ifrs-full:Revenue", b"uk-gaap-pt:TurnoverRevenue")
        .replace(b"ifrs-full:Assets", b"uk-gaap-pt:TotalAssets")
        .replace(b"ifrs-full:Liabilities", b"uk-gaap-pt:TotalLiabilities")
        .replace(b"ifrs-full:Equity", b"uk-gaap-pt:ShareholdersFunds")
        .replace(
            b"ifrs-full:ProfitLossBeforeTax",
            b"uk-gaap-pt:ProfitLossOnOrdinaryActivitiesBeforeTax",
        )
        .replace(
            b'ifrs-full:ProfitLoss"',
            b'uk-gaap-pt:ProfitLossForFinancialYear"',
        )
    )

    financials = extract_filing_financials(_document(report), _filing())
    codes = {
        item.code
        for statement in financials.statements
        for item in statement.line_items
    }

    assert {"revenue", "profit_before_tax", "net_income_loss"} <= codes
    assert {"total_assets", "total_liabilities", "total_equity"} <= codes


def test_untagged_html_has_no_structured_financials() -> None:
    with pytest.raises(FinancialsUnavailableError, match="numeric facts"):
        extract_filing_financials(
            _document(b"<html><body>Annual report</body></html>"), _filing()
        )


def test_structured_financials_round_trip_through_cache(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    financials = extract_filing_financials(_document(_ixbrl()), _filing())

    cache.put_financials(financials)
    restored = cache.get_financials(financials.filing_id)
    stats = cache.stats()
    cache.close()

    assert restored is not None
    assert restored.from_cache is True
    assert restored.statements == financials.statements
    assert stats.financial_reports == 1
    assert stats.compressed_financial_bytes > 0


def test_filings_collection_and_document_sections() -> None:
    older = _filing().model_copy(update={"filing_date": date(2024, 3, 1)})
    newer = _filing().model_copy(
        update={"id": "uk_nsm_new", "filing_date": date(2025, 3, 1)}
    )
    collection = Filings([older, newer])

    assert collection.latest() == newer
    assert collection.filter(filing_type="ACS").head(1)[0] == older

    content = FilingContent(
        filing_id=newer.id,
        markdown="# Report\n\nIntro\n\n## Revenue\n\nRevenue rose.\n",
        source_url="https://example.test/report.xhtml",
        media_type="application/xhtml+xml",
        extraction_method="markdownify",
        quality=ExtractionQuality(score=100, status="good", warnings=()),
        sha256="a" * 64,
    )
    document = FilingDocument.from_content(content)
    assert document.section("revenue") is not None
    assert any(section.title == "Revenue" for section in document.search("rose"))


def _filing() -> Filing:
    return Filing(
        id="uk_nsm_report-1",
        company_id="uk_00000001",
        source="fca_nsm",
        source_id="report-1",
        title="Annual report",
        category="Annual Financial Report",
        filing_type="ACS",
        filing_date=date(2025, 3, 1),
        document_id="report.xhtml",
        source_url="https://example.test/report.xhtml",
    )


def _document(data: bytes) -> SourceDocument:
    return SourceDocument(
        data=data,
        media_type="application/xhtml+xml",
        source_url="https://example.test/report.xhtml",
    )


def _ixbrl() -> bytes:
    return b"""<!doctype html>
    <html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
          xmlns:xbrli="http://www.xbrl.org/2003/instance">
      <body>
        <ix:header><ix:resources>
          <xbrli:context id="fy-current"><xbrli:entity><xbrli:identifier
            scheme="lei">TEST</xbrli:identifier></xbrli:entity><xbrli:period>
            <xbrli:startDate>2024-01-01</xbrli:startDate>
            <xbrli:endDate>2024-12-31</xbrli:endDate>
          </xbrli:period></xbrli:context>
          <xbrli:context id="fy-previous"><xbrli:entity><xbrli:identifier
            scheme="lei">TEST</xbrli:identifier></xbrli:entity><xbrli:period>
            <xbrli:startDate>2023-01-01</xbrli:startDate>
            <xbrli:endDate>2023-12-31</xbrli:endDate>
          </xbrli:period></xbrli:context>
          <xbrli:context id="instant-current"><xbrli:entity><xbrli:identifier
            scheme="lei">TEST</xbrli:identifier></xbrli:entity><xbrli:period>
            <xbrli:instant>2024-12-31</xbrli:instant>
          </xbrli:period></xbrli:context>
          <xbrli:context id="segment-current"><xbrli:entity><xbrli:identifier
            scheme="lei">TEST</xbrli:identifier><xbrli:segment>
            <xbrli:explicitMember dimension="ifrs-full:SegmentsAxis">
              test:RetailMember</xbrli:explicitMember>
          </xbrli:segment></xbrli:entity><xbrli:period>
            <xbrli:startDate>2024-01-01</xbrli:startDate>
            <xbrli:endDate>2024-12-31</xbrli:endDate>
          </xbrli:period></xbrli:context>
          <xbrli:unit id="GBP"><xbrli:measure>iso4217:GBP</xbrli:measure></xbrli:unit>
          <xbrli:unit id="GBP-per-share"><xbrli:measure>iso4217:GBP</xbrli:measure>
            <xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
        </ix:resources></ix:header>
        <ix:nonFraction name="ifrs-full:Revenue" contextRef="fy-current"
          unitRef="GBP" decimals="-3" scale="3">1,234</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:Revenue" contextRef="fy-previous"
          unitRef="GBP" decimals="-3" scale="3">1,100</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:Revenue" contextRef="segment-current"
          unitRef="GBP" decimals="-3" scale="3">900</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:ProfitLossBeforeTax" contextRef="fy-current"
          unitRef="GBP" decimals="-3" scale="3">120</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:ProfitLoss" contextRef="fy-current"
          unitRef="GBP" decimals="-3" scale="3">95</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:BasicEarningsLossPerShare"
          contextRef="fy-current" unitRef="GBP-per-share" decimals="2">
          0.42</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:Assets" contextRef="instant-current"
          unitRef="GBP" decimals="-3" scale="3">5,000</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:Liabilities" contextRef="instant-current"
          unitRef="GBP" decimals="-3" scale="3">3,000</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:Equity" contextRef="instant-current"
          unitRef="GBP" decimals="-3" scale="3">2,000</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:CashFlowsFromUsedInOperatingActivities"
          contextRef="fy-current" unitRef="GBP" decimals="-3"
          scale="3">450</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:CashFlowsFromUsedInInvestingActivities"
          contextRef="fy-current" unitRef="GBP" decimals="-3"
          scale="3" sign="-">210</ix:nonFraction>
      </body>
    </html>"""
