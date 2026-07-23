from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from openfilings.adapters.sfc import CUIF_DATASET_URL, SfcClient
from openfilings.models import Filing
from openfilings.xbrl.sfc_cuif_structured import extract_sfc_cuif_balance_sheet


def _row(cuenta: str, nombre: str, valor: str, signo: str = "+") -> dict[str, object]:
    return {
        "tipo_entidad": "1",
        "nombre_tipo_entidad": "ESTABLECIMIENTOS BANCARIOS",
        "codigo_entidad": "1",
        "nombre_entidad": "BANCO DE BOGOTA S.A.",
        "fecha_corte": "2025-12-31T00:00:00.000",
        "cuenta": cuenta,
        "nombre_cuenta": nombre,
        "moneda": "0",
        "nombre_moneda": "Total",
        "signo_valor": signo,
        "valor": valor,
    }


def _filing(period_end: date) -> Filing:
    return Filing(
        id="co_sfc_filing_1",
        company_id="co_sfc_001_001",
        source="sfc",
        source_id="1",
        title="Estados Financieros",
        category="accounts",
        filing_type="annual",
        filing_date=period_end,
        published_at=period_end,
        period_end=period_end,
        document_id="1",
        media_type="application/pdf",
        pdf_available=True,
        source_url="https://example.test/report.pdf",
    )


@pytest.mark.asyncio
async def test_cuif_balance_sheet_rows_queries_only_the_recognized_codes() -> None:
    """The CUIF dataset returns every sub-account under a filter unless
    the query is scoped to specific account codes - an entity's full
    account tree can run into the thousands of rows."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(CUIF_DATASET_URL)
        assert request.url.params["tipo_entidad"] == "1"
        assert request.url.params["codigo_entidad"] == "1"
        assert request.url.params["fecha_corte"] == "2025-12-31T00:00:00.000"
        assert "cuenta in (" in request.url.params["$where"]
        return httpx.Response(
            200,
            json=[
                _row("100000", "ACTIVO", "136304038425065.77"),
                _row("200000", "PASIVO", "119714971717838.23"),
                _row("300000", "PATRIMONIO", "16589066707227.51"),
                _row("110000", "EFECTIVO", "8294197426503.0"),
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = SfcClient(client=http)
        rows = await source.cuif_balance_sheet_rows("1", "1", date(2025, 12, 31))

    assert rows is not None
    assert len(rows) == 4


@pytest.mark.asyncio
async def test_cuif_balance_sheet_rows_returns_none_when_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = SfcClient(client=http)
        rows = await source.cuif_balance_sheet_rows("1", "1", date(2025, 12, 31))

    assert rows is None


def test_extract_sfc_cuif_balance_sheet_maps_recognized_account_codes() -> None:
    filing = _filing(date(2025, 12, 31))
    rows = [
        _row("100000", "ACTIVO", "136000000000000"),
        _row("200000", "PASIVO", "119700000000000"),
        _row("300000", "PATRIMONIO", "16300000000000"),
        _row("110000", "EFECTIVO", "8294197426503"),
        _row("111500", "CAJA", "1000000"),  # not a recognized top-level code
    ]

    statement = extract_sfc_cuif_balance_sheet(rows, filing)

    assert statement is not None
    assert statement.statement_type == "balance_sheet"
    by_code = {item.code: item.values[0].value for item in statement.line_items}
    assert by_code == {
        "total_assets": Decimal("136000000000000"),
        "total_liabilities": Decimal("119700000000000"),
        "total_equity": Decimal("16300000000000"),
        "cash_and_cash_equivalents": Decimal("8294197426503"),
    }
    assert (
        by_code["total_liabilities"] + by_code["total_equity"]
        == by_code["total_assets"]
    )


def test_extract_sfc_cuif_balance_sheet_applies_negative_sign() -> None:
    filing = _filing(date(2025, 12, 31))
    rows = [_row("100000", "ACTIVO", "500", signo="-")]

    statement = extract_sfc_cuif_balance_sheet(rows, filing)

    assert statement is not None
    assert statement.line_items[0].values[0].value == Decimal("-500")


def test_extract_sfc_cuif_balance_sheet_returns_none_without_recognized_codes() -> None:
    filing = _filing(date(2025, 12, 31))
    rows = [_row("111500", "CAJA", "1000000.0")]

    assert extract_sfc_cuif_balance_sheet(rows, filing) is None
