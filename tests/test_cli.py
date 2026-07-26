from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from typer.testing import CliRunner

from openfilings.cli import app
from openfilings.models import (
    FilingFinancials,
    FinancialLineItem,
    FinancialStatement,
    FinancialValue,
    ReportingPeriod,
)
from openfilings.service import OpenFilingsService


def test_inspect_document_reports_quality_and_timing(tmp_path, monkeypatch) -> None:
    source = tmp_path / "announcement.html"
    source.write_text(
        "<html><body><h1>Results</h1><p>"
        + ("Revenue increased. " * 30)
        + "</p></body></html>",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["inspect-document", source.name])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["extraction_method"] == "markdownify"
    assert payload["quality"]["status"] == "good"
    assert payload["elapsed_seconds"] >= 0


def test_import_sedar_accepts_local_pdf(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "shopify-2025.pdf"
    pdf.write_bytes(b"%PDF-1.7 local filing")
    captured: dict[str, object] = {}

    class FakeService:
        async def __aenter__(self) -> FakeService:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def import_sedar_filing(
            self, company_id: str, **kwargs: object
        ) -> SimpleNamespace:
            captured["company_id"] = company_id
            captured.update(kwargs)
            return SimpleNamespace(id="ca_sedar_filing_123")

    monkeypatch.setattr(
        OpenFilingsService,
        "from_settings",
        staticmethod(FakeService),
    )
    result = CliRunner().invoke(
        app,
        [
            "import-sedar",
            "ca_sedar_tsx_SHOP",
            str(pdf),
            "--title",
            "2025 Annual Report",
            "--filing-date",
            "2026-03-12",
            "--period-end",
            "2025-12-31",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ca_sedar_filing_123" in result.output
    assert captured["company_id"] == "ca_sedar_tsx_SHOP"
    assert captured["document_data"] == pdf.read_bytes()
    assert str(captured["document_url"]).startswith("file://")


def test_financials_reports_failed_accounting_identity(monkeypatch) -> None:
    period = ReportingPeriod(
        id="instant-2025",
        end_date=date(2025, 12, 31),
        kind="instant",
    )

    def line_item(code: str, value: str) -> FinancialLineItem:
        return FinancialLineItem(
            code=code,
            name=code,
            concept=f"pdf-label:{code}",
            values=(FinancialValue(period=period, value=Decimal(value), unit="COP"),),
        )

    broken = FilingFinancials(
        filing_id="co_sfc_filing_test",
        company_id="co_sfc_company_test",
        source_url="https://example.test/filing.pdf",
        statements=(
            FinancialStatement(
                statement_type="balance_sheet",
                title="Balance sheet",
                currency="COP",
                line_items=(
                    line_item("total_assets", "100"),
                    line_item("total_liabilities", "60"),
                    line_item("total_equity", "30"),
                ),
            ),
        ),
        fact_count=3,
        sha256="a" * 64,
    )

    class FakeService:
        async def __aenter__(self) -> FakeService:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get_filing_financials(
            self, filing_id: str, *, refresh: bool
        ) -> FilingFinancials:
            assert filing_id == broken.filing_id
            assert refresh is False
            return broken

    monkeypatch.setattr(
        OpenFilingsService,
        "from_settings",
        staticmethod(FakeService),
    )

    result = CliRunner().invoke(app, ["financials", broken.filing_id])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["validation"]["ok"] is False
    assert payload["validation"]["checks_failed"] == 1
    assert payload["validation"]["findings"] == [
        {
            "rule_id": "EQ.accounting_equation",
            "description": "assets = liabilities + equity",
            "period": "2025-12-31",
            "expected": 90.0,
            "actual": 100.0,
            "difference": 10.0,
        }
    ]
    assert (
        "Warning: 1 accounting-identity check(s) failed - this filing's figures "
        "may be inconsistent (see 'validation' in the output)." in result.stderr
    )
