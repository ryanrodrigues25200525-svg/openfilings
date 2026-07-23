from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest

from openfilings.exceptions import FinancialsUnavailableError
from openfilings.models import Filing
from openfilings.xbrl.cvm_structured import extract_cvm_structured_financials


def _filing() -> Filing:
    return Filing(
        id="br_cvm_test",
        company_id="br_cvm_009512",
        source="cvm",
        source_id="test",
        title="DFP 2025",
        category="accounts",
        filing_type="annual",
        filing_date=date(2026, 3, 5),
        period_end=date(2025, 12, 31),
        document_id="test",
        media_type="application/zip",
        issuer_name="Petrobras",
        pdf_available=False,
        source_url="https://example.test/dfp.zip",
    )


def _archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("dfp_cia_aberta_2025.csv", _master_csv())
        archive.writestr("dfp_cia_aberta_BPA_con_2025.csv", _bpa_csv())
        archive.writestr("dfp_cia_aberta_BPP_con_2025.csv", _bpp_csv())
        archive.writestr("dfp_cia_aberta_DRE_con_2025.csv", _dre_csv())
    return buffer.getvalue()


def test_extract_cvm_structured_financials_matches_real_petrobras_figures() -> None:
    """Values below are the real, verified Petrobras 2025 DFP figures (also
    confirmed independently via the PDF-heuristic path earlier)."""
    financials = extract_cvm_structured_financials(
        _archive(),
        _filing(),
        cd_cvm="009512",
        source_url="https://example.test/dfp_cia_aberta_2025.zip",
        sha256="a" * 64,
    )

    assert financials.extraction_method == "cvm-open-data"

    balance = financials.balance_sheet()
    assert balance is not None
    assert balance.currency == "BRL"
    codes = {item.code: item.values[0].value for item in balance.line_items}
    assert codes["total_assets"] == Decimal("1223389000000")
    assert codes["current_assets"] == Decimal("140026000000")
    assert codes["noncurrent_assets"] == Decimal("1083363000000")
    assert codes["current_liabilities"] == Decimal("198368000000")
    assert codes["noncurrent_liabilities"] == Decimal("607434000000")
    assert codes["total_equity"] == Decimal("417587000000")
    assert codes["total_assets"] == (
        codes["current_liabilities"]
        + codes["noncurrent_liabilities"]
        + codes["total_equity"]
    )

    income = financials.income_statement()
    assert income is not None
    income_codes = {item.code: item.values[0].value for item in income.line_items}
    assert income_codes["revenue"] == Decimal("497549000000")
    assert income_codes["gross_profit"] == Decimal("236998000000")
    assert income_codes["net_income_loss"] == Decimal("110605000000")
    assert income_codes["revenue"] + income_codes["cost_of_revenue"] == (
        income_codes["gross_profit"]
    )


def test_shallower_account_wins_on_label_collision() -> None:
    """"Estoques" (inventory) legitimately labels two different accounts in
    Petrobras' real chart of accounts: the current-asset line (1.01.04) and
    an unrelated nested long-term breakdown (1.02.01.05, correctly 0). The
    shallower, primary account must win regardless of file order."""
    financials = extract_cvm_structured_financials(
        _archive(),
        _filing(),
        cd_cvm="009512",
        source_url="https://example.test/dfp_cia_aberta_2025.zip",
        sha256="b" * 64,
    )
    balance = financials.balance_sheet()
    assert balance is not None
    inventory = next(item for item in balance.line_items if item.code == "inventory")
    assert inventory.values[0].value == Decimal("45173000000")


def test_extract_cvm_structured_financials_raises_when_company_absent() -> None:
    with pytest.raises(FinancialsUnavailableError):
        extract_cvm_structured_financials(
            _archive(),
            _filing(),
            cd_cvm="999999",
            source_url="https://example.test/dfp_cia_aberta_2025.zip",
            sha256="c" * 64,
        )


def _master_csv() -> str:
    return (
        "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;CATEG_DOC;ID_DOC;DT_RECEB;LINK_DOC\n"
        "33.000.167/0001-01;2025-12-31;1;PETROBRAS;009512;DFP;123456;"
        "2026-03-05;http://example.test\n"
    )


def _bpa_csv() -> str:
    header = (
        "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;GRUPO_DFP;MOEDA;ESCALA_MOEDA;"
        "ORDEM_EXERC;DT_FIM_EXERC;CD_CONTA;DS_CONTA;VL_CONTA;ST_CONTA_FIXA\n"
    )
    rows = [
        ("1", "Ativo Total", "1223389000", "2025-12-31", "ULTIMO"),
        ("1", "Ativo Total", "1124797000", "2024-12-31", "PENULTIMO"),
        ("1.01", "Ativo Circulante", "140026000", "2025-12-31", "ULTIMO"),
        ("1.01", "Ativo Circulante", "135212000", "2024-12-31", "PENULTIMO"),
        ("1.01.03", "Contas a Receber", "25461000", "2025-12-31", "ULTIMO"),
        ("1.01.03", "Contas a Receber", "22080000", "2024-12-31", "PENULTIMO"),
        ("1.01.04", "Estoques", "45173000", "2025-12-31", "ULTIMO"),
        ("1.01.04", "Estoques", "41550000", "2024-12-31", "PENULTIMO"),
        ("1.02", "Ativo Nao Circulante", "1083363000", "2025-12-31", "ULTIMO"),
        ("1.02", "Ativo Nao Circulante", "989585000", "2024-12-31", "PENULTIMO"),
        ("1.02.01.05", "Estoques", "0", "2025-12-31", "ULTIMO"),
        ("1.02.01.05", "Estoques", "0", "2024-12-31", "PENULTIMO"),
    ]
    return header + "".join(_bpa_row(*row) for row in rows)


def _bpa_row(
    code: str, label: str, value: str, period_end: str, order: str
) -> str:
    return (
        f"33.000.167/0001-01;2025-12-31;1;PETROBRAS;009512;"
        f"DF Consolidado - Balanco Patrimonial Ativo;REAL;MIL;{order};"
        f"{period_end};{code};{label};{value}.0000000000;S\n"
    )


def _bpp_csv() -> str:
    header = (
        "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;GRUPO_DFP;MOEDA;ESCALA_MOEDA;"
        "ORDEM_EXERC;DT_FIM_EXERC;CD_CONTA;DS_CONTA;VL_CONTA;ST_CONTA_FIXA\n"
    )
    rows = [
        ("2.01", "Passivo Circulante", "198368000", "2025-12-31", "ULTIMO"),
        ("2.01", "Passivo Circulante", "194808000", "2024-12-31", "PENULTIMO"),
        ("2.02", "Passivo Nao Circulante", "607434000", "2025-12-31", "ULTIMO"),
        ("2.02", "Passivo Nao Circulante", "562475000", "2024-12-31", "PENULTIMO"),
        (
            "2.03",
            "Patrimonio Liquido Consolidado",
            "417587000",
            "2025-12-31",
            "ULTIMO",
        ),
        (
            "2.03",
            "Patrimonio Liquido Consolidado",
            "367514000",
            "2024-12-31",
            "PENULTIMO",
        ),
    ]
    return header + "".join(_bpa_row(*row) for row in rows)


def _dre_csv() -> str:
    header = (
        "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;GRUPO_DFP;MOEDA;ESCALA_MOEDA;"
        "ORDEM_EXERC;DT_INI_EXERC;DT_FIM_EXERC;CD_CONTA;DS_CONTA;VL_CONTA;"
        "ST_CONTA_FIXA\n"
    )
    rows = [
        (
            "3.01",
            "Receita de Venda de Bens e/ou Servicos",
            "497549000",
            "2025-01-01",
            "2025-12-31",
            "ULTIMO",
        ),
        (
            "3.01",
            "Receita de Venda de Bens e/ou Servicos",
            "490829000",
            "2024-01-01",
            "2024-12-31",
            "PENULTIMO",
        ),
        (
            "3.02",
            "Custo dos Bens e/ou Servicos Vendidos",
            "-260551000",
            "2025-01-01",
            "2025-12-31",
            "ULTIMO",
        ),
        (
            "3.02",
            "Custo dos Bens e/ou Servicos Vendidos",
            "-244367000",
            "2024-01-01",
            "2024-12-31",
            "PENULTIMO",
        ),
        (
            "3.03",
            "Resultado Bruto",
            "236998000",
            "2025-01-01",
            "2025-12-31",
            "ULTIMO",
        ),
        (
            "3.03",
            "Resultado Bruto",
            "246462000",
            "2024-01-01",
            "2024-12-31",
            "PENULTIMO",
        ),
        (
            "3.11",
            "Lucro/Prejuizo Consolidado do Periodo",
            "110605000",
            "2025-01-01",
            "2025-12-31",
            "ULTIMO",
        ),
        (
            "3.11",
            "Lucro/Prejuizo Consolidado do Periodo",
            "37009000",
            "2024-01-01",
            "2024-12-31",
            "PENULTIMO",
        ),
    ]
    return header + "".join(_dre_row(*row) for row in rows)


def _dre_row(
    code: str,
    label: str,
    value: str,
    period_start: str,
    period_end: str,
    order: str,
) -> str:
    return (
        f"33.000.167/0001-01;2025-12-31;1;PETROBRAS;009512;"
        f"DF Consolidado - Demonstracao do Resultado;REAL;MIL;{order};"
        f"{period_start};{period_end};{code};{label};{value}.0000000000;S\n"
    )
