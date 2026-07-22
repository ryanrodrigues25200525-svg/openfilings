from __future__ import annotations

import io
import zipfile
from datetime import date

import httpx
import pytest

from openfilings.adapters.cvm import CvmClient
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache

COMPANY_URL_PATH = "/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
IPE_URL_PATH = "/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_2026.zip"
DOCUMENT_PATH = "/ENET/frmDownloadDocumento.aspx"
DOCUMENT_URL = (
    "https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?"
    "Tela=ext&descTipo=IPE&CodigoInstituicao=1&numProtocolo=1500726&"
    "numSequencia=1025432&numVersao=1"
)


@pytest.mark.asyncio
async def test_search_companies_returns_only_active_exchange_listings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == COMPANY_URL_PATH
        return httpx.Response(200, content=_company_csv())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = CvmClient(client=http, today=lambda: date(2026, 7, 22))
        companies = await source.search_companies("Banco do Brasil")

    assert len(companies) == 1
    company = companies[0]
    assert company.id == "br_cvm_001023"
    assert company.source_id == "00.000.000/0001-91"
    assert company.local_code == "001023"
    assert company.name == "BANCO DO BRASIL S.A."
    assert company.market == "BR"
    assert company.country_code == "BR"
    assert company.sources == ("cvm",)
    assert company.status == "active listed issuer"


@pytest.mark.asyncio
async def test_list_filings_maps_accounts_and_ignores_unrelated_documents() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == COMPANY_URL_PATH:
            return httpx.Response(200, content=_company_csv())
        assert request.url.path == IPE_URL_PATH
        return httpx.Response(200, content=_ipe_archive())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = CvmClient(client=http, today=lambda: date(2026, 7, 22))
        filings = await source.list_filings("br_cvm_001023", limit=2)

    assert [filing.id for filing in filings] == [
        "br_cvm_1046308",
        "br_cvm_1025432",
    ]
    interim, annual = filings
    assert annual.source == "cvm"
    assert annual.company_id == "br_cvm_001023"
    assert annual.filing_type == "annual"
    assert annual.category == "accounts"
    assert annual.period_end == date(2025, 12, 31)
    assert annual.filing_date == date(2026, 4, 2)
    assert annual.document_id == DOCUMENT_URL
    assert annual.pdf_available is True
    assert annual.xbrl_available is False
    assert interim.filing_type == "interim"


@pytest.mark.asyncio
async def test_list_filings_rejects_non_exchange_company_code() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_company_csv())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = CvmClient(client=http)
        with pytest.raises(SourceError, match="not an active Brazilian exchange"):
            await source.list_filings("br_cvm_004170")


@pytest.mark.asyncio
async def test_download_document_returns_pdf_with_provenance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == DOCUMENT_PATH
        return httpx.Response(
            200,
            # RAD serves real PDFs with this incorrect media type in production.
            headers={"content-type": "text/html"},
            content=b"%PDF-1.7 test report",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        source = CvmClient(client=http)
        document = await source.download_document(DOCUMENT_URL)

    assert document.data.startswith(b"%PDF")
    assert document.media_type == "application/pdf"
    assert document.source_url == DOCUMENT_URL
    assert document.profile is None


def test_document_url_rejects_external_hosts() -> None:
    with pytest.raises(DocumentUnavailableError, match="Unsafe"):
        CvmClient.document_url("https://example.test/report.pdf")


@pytest.mark.asyncio
async def test_service_runs_complete_cvm_search_list_and_markdown_pipeline(
    tmp_path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == COMPANY_URL_PATH:
            return httpx.Response(200, content=_company_csv())
        if request.url.path == IPE_URL_PATH:
            return httpx.Response(200, content=_ipe_archive())
        if request.url.path == DOCUMENT_PATH:
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF-1.7 test report",
            )
        raise AssertionError(f"Unexpected CVM request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        cvm = CvmClient(client=http, today=lambda: date(2026, 7, 22))
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(
            cache,
            cvm_source=cvm,
            converter=lambda _: "## Financial statements\n\nRevenue was R$ 100.",
        )

        company = await service.company("Banco do Brasil", source="cvm")
        filings = await company.get_filings(source="cvm", limit=1)
        filing = filings.latest()
        assert filing is not None
        content = await filing.markdown()
        cache.close()

    assert company.id == "br_cvm_001023"
    assert filing.id == "br_cvm_1046308"
    assert "## Financial statements" in content
    assert "Source system: `cvm`" in content


def _company_csv() -> bytes:
    header = (
        "CNPJ_CIA;DENOM_SOCIAL;DENOM_COMERC;SIT;CD_CVM;SETOR_ATIV;TP_MERC;"
        "CATEG_REG;SIT_EMISSOR;LOGRADOURO;COMPL;BAIRRO;MUN;UF;PAIS\n"
    )
    rows = (
        "00.000.000/0001-91;BANCO DO BRASIL S.A.;BANCO DO BRASIL;ATIVO;1023;"
        "Bancos;BOLSA;Categoria A;FASE OPERACIONAL;SAUN Quadra 5;Lote B;Asa Norte;"
        "BRASÍLIA;DF;BRASIL\n"
        "33.592.510/0001-54;VALE S.A.;VALE;ATIVO;4170;Mineração;"
        "BALCÃO ORGANIZADO;Categoria A;FASE OPERACIONAL;;;;RIO DE JANEIRO;RJ;BRASIL\n"
        "11.111.111/0001-11;EMPRESA CANCELADA S.A.;CANCELADA;CANCELADO;9999;"
        "Serviços;BOLSA;Categoria A;FASE OPERACIONAL;;;;SÃO PAULO;SP;BRASIL\n"
    )
    return (header + rows).encode("cp1252")


def _ipe_archive() -> bytes:
    header = (
        "CNPJ_Companhia;Nome_Companhia;Codigo_CVM;Data_Referencia;Categoria;Tipo;"
        "Especie;Assunto;Data_Entrega;Tipo_Apresentacao;Protocolo_Entrega;Versao;"
        "Link_Download\n"
    )
    annual = (
        "00.000.000/0001-91;BANCO DO BRASIL S.A.;1023;2025-12-31;"
        "Dados Econômico-Financeiros;Demonstrações Financeiras Anuais Completas;;;"
        "2026-04-02;AP - Apresentação;001023IPE311220250153281506-69;1;"
        f"{DOCUMENT_URL}\n"
    )
    interim = (
        "00.000.000/0001-91;BANCO DO BRASIL S.A.;1023;2026-03-31;"
        "Dados Econômico-Financeiros;Demonstrações Financeiras Intermediárias;;"
        "Demonstrações Contábeis 1T26;2026-05-13;AP - Apresentação;"
        "001023IPE310320260134003752-83;1;"
        "https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?Tela=ext&"
        "descTipo=IPE&CodigoInstituicao=1&numProtocolo=1521602&"
        "numSequencia=1046308&numVersao=1\n"
    )
    unrelated = (
        "00.000.000/0001-91;BANCO DO BRASIL S.A.;1023;2026-05-28;"
        "Comunicado ao Mercado;Outros Comunicados;;;2026-05-28;AP - Apresentação;"
        "001023IPE280520260108134349-54;1;"
        "https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?Tela=ext&"
        "descTipo=IPE&CodigoInstituicao=1&numProtocolo=1528046&"
        "numSequencia=1052752&numVersao=1\n"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "ipe_cia_aberta_2026.csv",
            (header + annual + interim + unrelated).encode("cp1252"),
        )
    return stream.getvalue()
