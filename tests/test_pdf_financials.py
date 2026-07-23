from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import FinancialsUnavailableError
from openfilings.models import Filing
from openfilings.xbrl import extract_filing_financials
from openfilings.xbrl.pdf_statements import (
    extract_pdf_ocr_financials,
    extract_pdf_source_financials,
    extract_pdf_table_financials,
    extract_pdf_text_financials,
)


def test_english_pdf_tables_build_scaled_financial_statements() -> None:
    financials = extract_pdf_table_financials(
        _english_report(),
        _filing(period_end=date(2025, 6, 30)),
        source_url="https://example.test/annual-report.pdf",
        sha256="a" * 64,
    )

    assert financials.extraction_method == "pdf-markdown-tables"
    assert financials.taxonomy_namespaces == ("pdf-derived",)
    assert financials.fact_count == 18
    assert [statement.statement_type for statement in financials.statements] == [
        "income_statement",
        "balance_sheet",
        "cash_flow_statement",
    ]

    income = financials.income_statement()
    assert income is not None
    assert income.currency == "SGD"
    revenue = next(item for item in income.line_items if item.code == "revenue")
    assert revenue.concept == "pdf-label:revenue"
    assert [value.value for value in revenue.values] == [
        Decimal("1234000000"),
        Decimal("1100000000"),
    ]
    assert revenue.values[0].period.start_date == date(2024, 7, 1)
    assert revenue.values[0].period.end_date == date(2025, 6, 30)
    assert revenue.values[0].decimals == "-6"

    balance_sheet = financials.balance_sheet()
    assert balance_sheet is not None
    assets = next(
        item for item in balance_sheet.line_items if item.code == "total_assets"
    )
    assert assets.values[0].period.kind == "instant"
    assert assets.values[0].value == Decimal("5000000000")

    cash_flow = financials.cash_flow_statement()
    assert cash_flow is not None
    investing = next(
        item for item in cash_flow.line_items if item.code == "investing_cash_flow"
    )
    assert investing.values[0].value == Decimal("-210000000")


@pytest.mark.parametrize(
    ("markdown", "currency", "expected_revenue"),
    [
        (
            """
            # Demonstrações financeiras consolidadas

            R$ milhões

            |  | 2025 | 2024 |
            | --- | ---: | ---: |
            | Receita líquida | 2.500 | 2.100 |
            | Lucro operacional | 420 | 390 |
            | Lucro antes do imposto de renda | 380 | 350 |
            """,
            "BRL",
            Decimal("2500000000"),
        ),
        (
            """
            # 合併綜合損益表

            新台幣仟元

            | 項目 | 2025 | 2024 |
            | --- | ---: | ---: |
            | 營業收入 | 1,200,000 | 1,050,000 |
            | 營業利益 | 180,000 | 160,000 |
            | 稅前淨利 | 150,000 | 140,000 |
            """,
            "TWD",
            Decimal("1200000000"),
        ),
    ],
)
def test_pdf_tables_support_portuguese_and_traditional_chinese_labels(
    markdown: str,
    currency: str,
    expected_revenue: Decimal,
) -> None:
    financials = extract_pdf_table_financials(
        markdown,
        _filing(period_end=date(2025, 12, 31)),
        source_url="https://example.test/report.pdf",
        sha256="b" * 64,
    )

    income = financials.income_statement()
    assert income is not None
    assert income.currency == currency
    revenue = next(item for item in income.line_items if item.code == "revenue")
    assert revenue.values[0].value == expected_revenue


def test_pdf_table_extraction_rejects_unrelated_and_single_metric_tables() -> None:
    markdown = """
    # Five-year overview

    | Statistic | 2025 | 2024 |
    | --- | ---: | ---: |
    | Employees | 10,000 | 9,500 |
    | Revenue | 100 | 90 |
    """

    with pytest.raises(FinancialsUnavailableError, match="supported statement"):
        extract_pdf_table_financials(
            markdown,
            _filing(),
            source_url="https://example.test/report.pdf",
            sha256="c" * 64,
        )


def test_aligned_pdf_text_builds_group_financial_statements() -> None:
    financials = extract_pdf_text_financials(
        (_sgx_income_text(), _sgx_position_text(), _sgx_cash_flow_text()),
        _filing(period_end=date(2025, 6, 30)),
        source_url="https://example.test/sgx-report.pdf",
        sha256="d" * 64,
    )

    assert financials.extraction_method == "pdf-aligned-text"
    income = financials.income_statement()
    assert income is not None
    assert income.currency == "SGD"
    assert next(item for item in income.line_items if item.code == "revenue").values[
        0
    ].value == Decimal("1298167000")
    assert next(
        item for item in income.line_items if item.code == "operating_income_loss"
    ).values[0].value == Decimal("742843000")
    assert next(
        item for item in income.line_items if item.code == "profit_before_tax"
    ).values[0].value == Decimal("785891000")

    balance = financials.balance_sheet()
    assert balance is not None
    assert next(
        item for item in balance.line_items if item.code == "total_assets"
    ).values[0].value == Decimal("4143988000")

    cash_flow = financials.cash_flow_statement()
    assert cash_flow is not None
    assert next(
        item for item in cash_flow.line_items if item.code == "operating_cash_flow"
    ).values[0].value == Decimal("841670000")


def test_aligned_pdf_text_finds_total_behind_unlabeled_segment_breakdown() -> None:
    """Revenue can be broken down by segment with no repeated "Total revenue"
    label before its own total row appears - the total must not be confused
    with the first segment's own values."""
    financials = extract_pdf_text_financials(
        (_hkex_income_text(),),
        _filing(period_end=date(2024, 12, 31), source="hkex", filing_type="annual"),
        source_url="https://example.test/hkex-report.pdf",
        sha256="f" * 64,
    )

    income = financials.income_statement()
    assert income is not None
    assert next(
        item for item in income.line_items if item.code == "revenue"
    ).values[0].value == Decimal("660257000000")
    assert next(
        item for item in income.line_items if item.code == "cost_of_revenue"
    ).values[0].value == Decimal("-311011000000")
    assert next(
        item for item in income.line_items if item.code == "gross_profit"
    ).values[0].value == Decimal("349246000000")


def test_financial_extractor_routes_pdf_documents_to_table_parser(monkeypatch) -> None:
    monkeypatch.setattr(
        "openfilings.xbrl.pdf_statements._pdf_statement_sections", lambda _: ()
    )
    monkeypatch.setattr(
        "openfilings.xbrl.pdf_statements.pdf_to_markdown", lambda _: _english_report()
    )
    document = SourceDocument(
        data=b"%PDF-1.7 synthetic",
        media_type="application/pdf",
        source_url="https://example.test/annual-report.pdf",
    )

    financials = extract_filing_financials(
        document,
        _filing(period_end=date(2025, 6, 30)),
    )

    assert financials.extraction_method == "pdf-markdown-tables"
    assert financials.source_url == document.source_url
    assert financials.sha256 != ""


def test_interim_pdf_values_use_year_to_date_reporting_period() -> None:
    filing = _filing(
        period_end=date(2025, 3, 31),
        source="cvm",
        filing_type="interim",
    )

    financials = extract_pdf_text_financials(
        (
            """
            Demonstração do resultado
            Período encerrado em 31 de março de 2025
            R$ milhares
            2025
            2024
            Receita líquida
            2.500
            2.100
            Lucro operacional
            420
            390
            Lucro antes do imposto de renda
            380
            350
            """,
        ),
        filing,
        source_url="https://example.test/interim.pdf",
        sha256="e" * 64,
    )

    income = financials.income_statement()
    assert income is not None
    value = next(item for item in income.line_items if item.code == "revenue").values[0]
    assert value.period.start_date == date(2025, 1, 1)
    assert value.period.end_date == date(2025, 3, 31)
    assert value.period.fiscal_period == "Q"


def test_image_only_pdf_reports_clear_ocr_requirement() -> None:
    import pymupdf

    document = pymupdf.open()
    document.new_page()
    pdf_bytes = document.tobytes()
    document.close()

    with pytest.raises(FinancialsUnavailableError, match=r"image-only.*OCR"):
        extract_pdf_source_financials(
            pdf_bytes,
            _filing(source="twse"),
            source_url="https://example.test/scanned.pdf",
            sha256="f" * 64,
        )


def test_ocr_pages_build_structured_financials() -> None:
    markdown = "\n\n".join(
        (
            "## Page 1\n\nCover page",
            f"## Page 2\n\n{_sgx_income_text()}",
            f"## Page 3\n\n{_sgx_position_text()}",
            f"## Page 4\n\n{_sgx_cash_flow_text()}",
        )
    )

    financials = extract_pdf_ocr_financials(
        markdown,
        _filing(period_end=date(2025, 6, 30)),
        source_url="https://example.test/scanned.pdf",
        sha256="1" * 64,
    )

    assert financials.extraction_method == "pdf-ocr-text"
    assert financials.fact_count >= 6
    assert financials.income_statement() is not None
    assert financials.balance_sheet() is not None
    assert financials.cash_flow_statement() is not None


def _filing(
    *,
    period_end: date = date(2025, 12, 31),
    source: str = "sgx",
    filing_type: str = "annual",
) -> Filing:
    return Filing(
        id="sg_sgx_report",
        company_id="sg_sgx_1J26",
        source=source,  # type: ignore[arg-type]
        source_id="report",
        title="2025 Annual Report",
        category="accounts",
        filing_type=filing_type,
        filing_date=date(2026, 3, 1),
        period_end=period_end,
        document_id="https://example.test/report.pdf",
        media_type="application/pdf",
        issuer_name="Example Limited",
        pdf_available=True,
        source_url="https://example.test/report.pdf",
    )


def _english_report() -> str:
    return """
    # Consolidated income statement

    S$ million

    |  | 2025 | 2024 |
    | --- | ---: | ---: |
    | Revenue | 1,234 | 1,100 |
    | Operating profit | 300 | 250 |
    | Profit before tax | 280 | 230 |
    | Profit for the year | 210 | 180 |

    # Statements of financial position

    S$ million

    |  | 2025 | 2024 |
    | --- | ---: | ---: |
    | Total assets | 5,000 | 4,500 |
    | Total liabilities | 3,000 | 2,700 |
    | Total equity | 2,000 | 1,800 |

    # Consolidated statement of cash flows

    S$ million

    |  | 2025 | 2024 |
    | --- | ---: | ---: |
    | Net cash from operating activities | 450 | 400 |
    | Net cash used in investing activities | (210) | (180) |
    """


def _hkex_income_text() -> str:
    return """
    Consolidated Income Statement
    For the year ended 31 December 2024
    Year ended 31 December
    2024
    2023
    Note
    RMB'Million
    RMB'Million
    Revenues
    Value-added Services
    319,168
    298,375
    Marketing Services
    121,374
    101,482
    FinTech and Business Services
    211,956
    203,763
    Others
    7,759
    5,395
    6
    660,257
    609,015
    Cost of revenues
    7
    (311,011)
    (315,906)
    Gross profit
    349,246
    293,109
    """


def _sgx_income_text() -> str:
    return """
    Statement of Comprehensive Income
    For the financial year ended 30 June 2025
    Group
    Company
    Note
    2025
    $'000
    2024
    $'000
    2025
    $'000
    2024
    $'000
    Operating revenue
    Fixed Income, Currencies and Commodities
    4
    350,059
    322,497
    -
    -
    Operating revenue less transaction-based expenses
    (net revenue)
    1,298,167
    1,162,028
    802,349
    760,046
    Operating profit
    8
    742,843
    606,382
    508,099
    480,564
    Profit before tax
    785,891
    709,017
    541,019
    272,343
    Net profit after tax
    648,127
    597,578
    535,455
    268,887
    """


def _sgx_position_text() -> str:
    return """
    Statement of Financial Position
    As at 30 June 2025
    Group
    Company
    2025
    $'000
    2024
    $'000
    2025
    $'000
    2024
    $'000
    Cash and cash equivalents
    11
    1,129,979
    998,111
    260,731
    182,296
    Current assets
    2,449,142
    2,247,017
    533,684
    386,505
    Total assets
    4,143,988
    3,984,962
    2,349,749
    2,206,343
    Total liabilities
    1,944,000
    2,024,000
    1,000,000
    900,000
    Total equity
    2,199,988
    1,960,962
    1,349,749
    1,306,343
    """


def _sgx_cash_flow_text() -> str:
    return """
    Consolidated Statement of Cash Flows
    For the financial year ended 30 June 2025
    Group
    2025
    $'000
    2024
    $'000
    Net cash generated from operating activities
    841,670
    615,798
    Net cash used in investing activities
    (210,000)
    (180,000)
    Net cash used in financing activities
    (450,000)
    (420,000)
    """
