from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from openfilings.adapters.base import SourceDocument
from openfilings.domain import FilingDocument, Filings
from openfilings.exceptions import FinancialsUnavailableError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
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
    assert financials.income_statement() == income
    assert financials.balance_sheet() == financials.statements[1]
    assert financials.cash_flow_statement() == financials.statements[2]

    records = income.to_records()
    revenue_record = next(item for item in records if item["code"] == "revenue")
    assert revenue_record["FY 2024-12-31"] == Decimal("1234000")
    assert revenue_record["FY 2023-12-31"] == Decimal("1100000")

    markdown = financials.to_markdown()
    assert "## Income statement" in markdown
    assert "| Revenue | 1234000 | 1100000 |" in markdown

    cash_flow = financials.statements[2]
    investing = next(
        item for item in cash_flow.line_items if item.code == "investing_cash_flow"
    )
    assert investing.values[0].value == Decimal("-210000")


def test_total_liabilities_prefers_assets_minus_equity_derivation() -> None:
    """A filer that doesn't tag a bare "Liabilities" total (only current and
    non-current) can still have a liability bucket outside both (e.g. IFRS
    5's "liabilities included in disposal groups classified as held for
    sale"). Summing current + non-current would then be too small - assets
    minus equity is always exactly right when both are directly tagged."""
    old = (
        b'<ix:nonFraction name="ifrs-full:Liabilities" contextRef="instant-current"\n'
        b'          unitRef="GBP" decimals="-3" scale="3">3,000</ix:nonFraction>'
    )
    new = (
        b'<ix:nonFraction name="ifrs-full:CurrentLiabilities"'
        b' contextRef="instant-current"\n'
        b'          unitRef="GBP" decimals="-3" scale="3">1,000</ix:nonFraction>'
        b'<ix:nonFraction name="ifrs-full:NoncurrentLiabilities"'
        b' contextRef="instant-current"\n'
        b'          unitRef="GBP" decimals="-3" scale="3">1,500</ix:nonFraction>'
    )
    report = _ixbrl().replace(old, new)
    assert new in report and old not in report

    financials = extract_filing_financials(_document(report), _filing())

    balance = financials.balance_sheet()
    assert balance is not None
    by_code = {item.code: item for item in balance.line_items}
    total_liabilities = by_code["total_liabilities"]
    assert total_liabilities.concept == "derived:total_assets-total_equity"
    # Assets (5,000) minus equity (2,000) = 3,000 - not current (1,000) +
    # non-current (1,500) = 2,500, which would silently miss 500.
    assert total_liabilities.values[0].value == Decimal("3000000")


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


def test_current_ifrs_revenue_concept_is_normalized() -> None:
    report = _ixbrl().replace(
        b"ifrs-full:Revenue", b"ifrs-full:RevenueFromContractsWithCustomers"
    )

    financials = extract_filing_financials(_document(report), _filing())
    revenue = next(
        item for item in financials.statements[0].line_items if item.code == "revenue"
    )

    assert revenue.concept == "ifrs-full:RevenueFromContractsWithCustomers"
    assert revenue.values[0].value == Decimal("1234000")


def test_tagged_document_limit_supports_large_esef_reports() -> None:
    assert MAX_TAGGED_DOCUMENT_BYTES == 150 * 1024 * 1024


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


def test_document_sections_filter_out_decorative_headings() -> None:
    content = FilingContent(
        filing_id="decorative-headings",
        markdown=(
            "# Annual Report\n\n"
            "**Growing Momentum**\n\n"
            "###### $1.1b\n\n"
            "###### ~47 cts\n\n"
            "# On 27 February 2023 and 28 February 2023, the Asset Co Transaction "
            "and the Proposed Combination were completed following satisfaction of "
            "all conditions precedent under the transaction agreements\n\n"
            "## Balance Sheets\n\nTotal assets 1,000.\n"
        ),
        source_url="https://example.test/report.xhtml",
        media_type="application/xhtml+xml",
        extraction_method="markdownify",
        quality=ExtractionQuality(score=100, status="good", warnings=()),
        sha256="c" * 64,
    )
    document = FilingDocument.from_content(content)

    titles = {section.title for section in document.sections}
    assert "$1.1b" not in titles
    assert "~47 cts" not in titles
    assert not any(title.startswith("On 27 February 2023") for title in titles)
    assert document.section("balance sheets") is not None


def test_document_search_ranks_multi_term_section_matches() -> None:
    content = FilingContent(
        filing_id="ranked-search",
        markdown=(
            "# Report\n\nOverview.\n\n"
            "## Liquidity\n\nCash and liquidity remained strong.\n\n"
            "## Revenue growth\n\nRevenue growth accelerated across regions.\n\n"
            "## Risks\n\nRevenue may be affected by currency risk.\n"
        ),
        source_url="https://example.test/report.xhtml",
        media_type="application/xhtml+xml",
        extraction_method="markdownify",
        quality=ExtractionQuality(score=100, status="good", warnings=()),
        sha256="b" * 64,
    )
    document = FilingDocument.from_content(content)

    results = document.ranked_search("revenue growth")

    assert results[0].section.title == "Revenue growth"
    assert results[0].matched_terms == ("growth", "revenue")
    assert results[0].score > results[1].score
    assert document.search("revenue growth")[0].title == "Revenue growth"


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


def test_concept_priority_prefers_the_real_equity_total_over_the_parent_line() -> None:
    """IFRS balance sheets tag both the parent-attributable equity and the
    real total. Whichever the extractor keeps must be decided by the alias
    table, not document order: a Turkish balance sheet lists the parent line
    first, so order-based selection silently drops non-controlling interests
    and breaks assets = liabilities + equity by exactly the NCI. Confirmed
    live on BIM (KAP), whose equity was understated by 1,817,555 TRY."""
    from openfilings.xbrl.mappings import concept_priority, definition_for_concept

    total = "ifrs-full:Equity"
    parent = "ifrs-full:EquityAttributableToOwnersOfParent"

    assert definition_for_concept(total).code == "total_equity"
    assert definition_for_concept(parent).code == "total_equity"
    assert concept_priority(total) < concept_priority(parent)
    # An unmapped concept must rank worse than any real alias.
    assert concept_priority("ifrs-full:NotALineItem") > concept_priority(parent)
