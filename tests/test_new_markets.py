from __future__ import annotations

import io
import json
import zipfile
from datetime import date

import httpx
import pytest

from openfilings.adapters.base import SourceDocument
from openfilings.adapters.bmv import BmvClient
from openfilings.adapters.nse import NseClient
from openfilings.adapters.sedar import SedarClient
from openfilings.adapters.sfc import SfcClient
from openfilings.adapters.smv import SmvClient
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.extraction.document import extract_document
from openfilings.models import Filing
from openfilings.service import OpenFilingsService
from openfilings.storage.sqlite import SQLiteCache
from openfilings.xbrl import extract_filing_financials
from openfilings.xbrl.pdf_statements import extract_pdf_table_financials


@pytest.mark.asyncio
async def test_bmv_search_filings_and_download_use_equity_documents() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issuers-information"):
            return httpx.Response(200, text="<html></html>")
        if request.url.path.endswith("/doSearch"):
            assert request.url.params["idTipoMercado"] == "CGEN_CAPIT"
            payload = {
                "response": {
                    "resultado": [
                        {
                            "idEmisora": 6024,
                            "claveEmisora": "AMX",
                            "razonSocial": "AMERICA MOVIL, S.A.B. DE C.V.",
                        }
                    ]
                }
            }
            import json

            return httpx.Response(200, text=f"for(;;);({json.dumps(payload)})")
        if "/financialinformation/AMX-6024-" in request.url.path:
            xbrl_href = (
                "/docs-pub/ifrsxbrl/../visor/visorXbrl.html?"
                "docins=../ifrsxbrl/ifrsxbrl_1575696_2026-02_1.zip"
            )
            return httpx.Response(
                200,
                text=f"""
                <table><tbody><tr><td><span>21-Jul-2026 15:44</span></td>
                <td>Información Del Trimestre 2 Del Año 2026</td><td>
                <a href="{xbrl_href}">Download</a>
                </td></tr></tbody></table>
                <table><tbody><tr><td>28-Apr-2026 16:15</td>
                <td>Informe Anual en formato PDF del año 2025</td><td>
                <a href="/docs-pub/infoanua/infoanua_1552461_2025_1.pdf">Download</a>
                </td></tr></tbody></table>
                """,
            )
        if request.url.path.endswith("ifrsxbrl_1575696_2026-02_1.zip"):
            return httpx.Response(
                200,
                headers={"content-type": "application/zip"},
                content=b"PK\x03\x04report",
            )
        raise AssertionError(f"Unexpected BMV request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BmvClient(client=http)
        companies = await client.search_companies("AMX")
        filings = await client.list_filings(companies[0].id)
        document = await client.download_document(filings[0].document_id or "")

    assert companies[0].id == "mx_bmv_6024"
    assert companies[0].country_code == "MX"
    assert {filing.filing_type for filing in filings} == {"annual", "quarterly"}
    assert filings[0].xbrl_available
    assert document.media_type == "application/zip"
    assert document.profile == "bmv-json"
    with pytest.raises(DocumentUnavailableError):
        BmvClient.document_url("https://evil.example/docs-pub/infoanua/a.pdf")


def test_bmv_json_archive_produces_markdown_and_structured_statements() -> None:
    archive = _bmv_json_archive()
    document = SourceDocument(
        data=archive,
        media_type="application/zip",
        source_url="https://www.bmv.com.mx/docs-pub/ifrsxbrl/report.zip",
        profile="bmv-json",
    )
    filing = Filing(
        id="mx_bmv_filing_1",
        company_id="mx_bmv_6024",
        source="bmv",
        source_id="1",
        title="Q2 2026",
        category="accounts",
        filing_type="quarterly",
        filing_date="2026-07-21",
        period_end="2026-06-30",
        source_url=document.source_url,
    )

    extraction = extract_document(document)
    financials = extract_filing_financials(document, filing)

    assert extraction.method == "bmv-xbrl-json"
    assert "# AMX BMV IFRS filing — 2026-06-30" in extraction.markdown
    assert "Ingresos" in extraction.markdown
    assert financials.income_statement() is not None
    assert financials.balance_sheet() is not None
    assert financials.income_statement().currency == "MXN"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_nse_search_filings_and_download_are_indian_equities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("corporate-filings-annual-reports"):
            return httpx.Response(200, text="<html></html>")
        if request.url.path.endswith("EQUITY_L.csv"):
            return httpx.Response(
                200,
                content=(
                    b"SYMBOL,NAME OF COMPANY,SERIES,ISIN NUMBER\n"
                    b"RELIANCE,Reliance Industries Limited,EQ,INE002A01018\n"
                    b"NIFTYBEES,Nippon India ETF Nifty BeES,EQ,INF204KB14I2\n"
                ),
            )
        if request.url.path == "/api/annual-reports":
            assert request.url.params["symbol"] == "RELIANCE"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "companyName": "Reliance Industries Limited",
                            "fromYr": "2024",
                            "toYr": "2025",
                            "broadcast_dttm": "07-AUG-2025 11:44:57",
                            "fileName": "https://nsearchives.nseindia.com/annual_reports/AR_27322_RELIANCE_2024_2025_A.pdf",
                        }
                    ]
                },
            )
        if request.url.path.endswith("AR_27322_RELIANCE_2024_2025_A.pdf"):
            return httpx.Response(200, content=b"%PDF-1.7 report")
        raise AssertionError(f"Unexpected NSE request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = NseClient(client=http)
        companies = await client.search_companies("RELIANCE")
        filings = await client.list_filings(companies[0].id)
        document = await client.download_document(filings[0].document_id or "")

    assert [company.id for company in companies] == ["in_nse_RELIANCE"]
    assert companies[0].country_code == "IN"
    assert filings[0].period_end == date(2025, 3, 31)
    assert document.media_type == "application/pdf"




@pytest.mark.asyncio
async def test_sfc_filters_bvc_equities_and_downloads_financial_pdf() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["api-key"]
        if request.url.path.endswith("emisores-inscritos-vigentes/reporte"):
            equity = {
                "nombre": "ENTIDADES PUBLICAS",
                "rnCotien": "260",
                "rnCodent": "036",
                "nmEntidad": "ECOPETROL S.A.",
                "nrNitEntidad": 8999990681,
                "nmEspecie": "ACCION",
                "cdBolsa": "BVC",
            }
            return httpx.Response(200, json=[equity, {**equity, "nmEspecie": "BONO"}])
        if request.url.path.endswith("tipo-entidad/260/codigo-entidad/036"):
            return httpx.Response(
                200,
                json=[
                    {
                        "fechaRegistro": "2025-03-29T07:00:01.000-0500",
                        "resumen": "Estados Financieros consolidados 2024",
                        "entidad": {"razonSocial": "ECOPETROL S.A."},
                        "archivoInfoRelevante": {
                            "idArchivoInfoRelevante": 119502,
                            "contentType": "application/pdf",
                            "nombre": "Ecopetrol EEFF 2024.pdf",
                        },
                    }
                ],
            )
        if request.url.path.endswith("id-archivo/119502"):
            return httpx.Response(200, content=b"%PDF-1.7 report")
        raise AssertionError(f"Unexpected SFC request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SfcClient(client=http)
        companies = await client.search_companies("Ecopetrol")
        filings = await client.list_filings(companies[0].id)
        document = await client.download_document(filings[0].document_id or "")

    assert [company.id for company in companies] == ["co_sfc_260_036"]
    assert companies[0].country_code == "CO"
    assert filings[0].period_end == date(2024, 12, 31)
    assert document.media_type == "application/pdf"


@pytest.mark.asyncio
async def test_smv_builds_peru_filing_from_official_statement_rows() -> None:
    company_row = {
        "RPJ": "B30006",
        "TipoEmpresa": "EMPRESAS EMISORAS",
        "NombreEmpresa": "ALICORP S.A.A.",
        "RUC": "20100055237",
        "Ejercicio": "2023",
        "DescripcionCuenta": None,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/obtener_EFData"):
            assert body["rows"] == 500
            assert body["sidx"] == "RPJ"
            rows = [company_row]
            return httpx.Response(
                200,
                json={"d": {"rows": rows, "total": 1}},
            )
        assert body["Ejercicio"] == "2023"
        assert body["Periodo"] == "A"
        assert body["Tipo"] in {"C", "I"}
        if request.url.path.endswith("/obtener_BalanceGeneral"):
            rows = [
                {
                    **company_row,
                    "DescripcionCuenta": "Total de activos",
                    "Monto1": 1000,
                    "Monto2": 900,
                },
                {
                    **company_row,
                    "DescripcionCuenta": "Total de pasivos",
                    "Monto1": 400,
                    "Monto2": 350,
                },
                {**company_row, "RPJ": "B30007"},
            ]
        else:
            rows = [
                {
                    **company_row,
                    "DescripcionCuenta": "Total de activos",
                    "Monto1": 1000,
                    "Monto2": 900,
                },
                {**company_row, "RPJ": "B30007"},
            ]
        return httpx.Response(200, json={"d": json.dumps(rows)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SmvClient(client=http, today=lambda: date(2023, 12, 31))
        companies = await client.search_companies("Alicorp")
        filings = await client.list_filings(companies[0].id)
        document = await client.download_document(filings[0].document_id or "")

    assert companies[0].id == "pe_smv_B30006"
    assert companies[0].country_code == "PE"
    assert filings[0].period_end == date(2023, 12, 31)
    assert document.media_type == "text/html"
    assert b"Estado de situaci" in document.data
    assert b"Total de activos" in document.data
    assert b"Total de pasivos" in document.data
    financials = extract_filing_financials(document, filings[0])
    assert financials.balance_sheet() is not None
    assert financials.balance_sheet().currency == "PEN"  # type: ignore[union-attr]
    assert financials.extraction_method == "smv-open-data-tables"


@pytest.mark.asyncio
async def test_canada_search_is_tsx_only_and_sedar_limitation_is_explicit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/tsxv/" in request.url.path:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={
                "results": [
                    {"symbol": "SHOP", "name": "Shopify Inc."},
                    {"symbol": "SHPU", "name": "SavvyLong Shopify ETF"},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = SedarClient(client=http)
        companies = await client.search_companies("SHOP")
        with pytest.raises(SourceError, match="blocks non-browser"):
            await client.list_filings(companies[0].id)

    assert [company.id for company in companies] == ["ca_sedar_tsx_SHOP"]
    assert companies[0].country_code == "CA"


@pytest.mark.asyncio
async def test_service_routes_registered_public_market_source(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/tsxv/" in request.url.path:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={"results": [{"symbol": "SHOP", "name": "Shopify Inc."}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sedar = SedarClient(client=http)
        cache = SQLiteCache(tmp_path / "cache.sqlite3")
        service = OpenFilingsService(cache, market_sources=(sedar,))
        companies = await service.search_companies("SHOP", source="sedar")
        cache.close()

    assert companies[0].sources == ("sedar",)


def test_spanish_statement_tables_map_to_normalized_financials() -> None:
    filing = Filing(
        id="co_sfc_filing_1",
        company_id="co_sfc_260_036",
        source="sfc",
        source_id="1",
        title="Estados Financieros 2024",
        category="accounts",
        filing_type="annual",
        filing_date="2025-03-29",
        period_end="2024-12-31",
        source_url="https://www.superfinanciera.gov.co/example.pdf",
    )
    markdown = """
    ## Estado de resultados

    | Cuenta | 2024 | 2023 |
    |---|---:|---:|
    | Ingresos de actividades ordinarias | 1,200 | 1,000 |
    | Utilidad neta | 240 | 200 |

    ## Estado de situación financiera

    | Cuenta | 2024 | 2023 |
    |---|---:|---:|
    | Total de activos | 2,000 | 1,800 |
    | Total de pasivos | 800 | 750 |

    ## Estado de flujos de efectivo

    | Cuenta | 2024 | 2023 |
    |---|---:|---:|
    """
    markdown += (
        "| Flujos de Efectivo y Equivalente al Efectivo Procedente de "
        "(Utilizados en) Actividades de Operación | 300 | 250 |\n"
        "| Flujos de Efectivo y Equivalente al Efectivo Procedente de "
        "(Utilizados en) Actividades de Inversión | -100 | -80 |\n"
        "| Flujos de Efectivo y Equivalente al Efectivo Procedente de "
        "(Utilizados en) Actividades de Financiación | -50 | -40 |\n"
    )

    financials = extract_pdf_table_financials(
        markdown,
        filing,
        source_url=filing.source_url,
        sha256="a" * 64,
    )

    assert financials.income_statement() is not None
    assert financials.balance_sheet() is not None
    assert financials.cash_flow_statement() is not None
    assert financials.income_statement().currency == "COP"  # type: ignore[union-attr]


def _bmv_json_archive() -> bytes:
    context_current = {
        "Periodo": {
            "FechaInstante": "2026-06-30T00:00:00Z",
            "FechaInicio": None,
            "FechaFin": None,
        },
        "ValoresDimension": [],
    }
    context_duration = {
        "Periodo": {
            "FechaInstante": None,
            "FechaInicio": "2026-01-01T00:00:00Z",
            "FechaFin": "2026-06-30T00:00:00Z",
        },
        "ValoresDimension": [],
    }
    concepts = {
        "ifrs-full_Revenue": _bmv_concept("Revenue", "Ingresos"),
        "ifrs-full_Assets": _bmv_concept("Assets", "Activos"),
    }
    facts = {
        "revenue": _bmv_fact(
            "ifrs-full_Revenue",
            "Revenue",
            "duration",
            "1000",
        ),
        "assets": _bmv_fact(
            "ifrs-full_Assets",
            "Assets",
            "current",
            "2000",
        ),
    }
    payload = {
        "EntidadesPorId": {"entity": {"Id": "AMX"}},
        "ContextosPorId": {
            "current": context_current,
            "duration": context_duration,
        },
        "UnidadesPorId": {
            "mxn": {"Medidas": [{"Etiqueta": "ISO4217:MXN"}]},
        },
        "HechosPorId": facts,
        "HechosPorIdConcepto": {
            "ifrs-full_Revenue": ["revenue"],
            "ifrs-full_Assets": ["assets"],
        },
        "Taxonomia": {
            "mapaPrefijos": {
                "http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full": "ifrs-full"
            },
            "ConceptosPorId": concepts,
            "RolesPresentacion": [
                {
                    "Nombre": "[210000] Estados financieros",
                    "Estructuras": [
                        {
                            "IdConcepto": "ifrs-full_Revenue",
                            "SubEstructuras": [
                                {
                                    "IdConcepto": "ifrs-full_Assets",
                                    "SubEstructuras": [],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.json", json.dumps(payload))
    return output.getvalue()


def _bmv_concept(name: str, spanish_label: str) -> dict[str, object]:
    return {
        "Nombre": name,
        "Etiquetas": {
            "es": {
                "http://www.xbrl.org/2003/role/label": {
                    "Valor": spanish_label,
                }
            }
        },
    }


def _bmv_fact(
    concept_id: str,
    local_name: str,
    context_id: str,
    value: str,
) -> dict[str, object]:
    return {
        "IdConcepto": concept_id,
        "NombreConcepto": local_name,
        "EspacioNombres": ("http://xbrl.ifrs.org/taxonomy/2017-03-09/ifrs-full"),
        "IdContexto": context_id,
        "IdUnidad": "mxn",
        "Decimales": "0",
        "Valor": value,
        "EsNumerico": True,
        "EsValorNil": False,
    }
