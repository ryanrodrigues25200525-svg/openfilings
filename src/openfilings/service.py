"""Application service shared by command-line and MCP interfaces."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from typing import Any, TypeVar, cast

from openfilings.adapters.asx import AsxClient
from openfilings.adapters.base import PublicMarketClient, SourceDocument
from openfilings.adapters.bmv import BmvClient
from openfilings.adapters.cvm import CvmClient
from openfilings.adapters.edinet import EdinetClient
from openfilings.adapters.esef import ENABLED_ESEF_MARKETS, EsefClient
from openfilings.adapters.fca_nsm import FcaNsmClient
from openfilings.adapters.nse import NseClient
from openfilings.adapters.sedar import (
    SedarClient,
    validate_sedar_document_url,
    validate_sedar_pdf,
)
from openfilings.adapters.sfc import CUIF_DATASET_URL, SfcClient
from openfilings.adapters.sgx import SgxClient
from openfilings.adapters.smv import SmvClient
from openfilings.config import Settings
from openfilings.domain import FilingDocument
from openfilings.exceptions import (
    CompanyNotFoundError,
    ConfigurationError,
    DocumentUnavailableError,
    ExtractionError,
    FilingNotFoundError,
    FinancialsUnavailableError,
    SourceError,
)
from openfilings.extraction.document import OcrConverter, extract_document
from openfilings.extraction.html import html_to_markdown
from openfilings.extraction.ocr import ocr_pdf_to_markdown, tesseract_available
from openfilings.extraction.pdf import pdf_to_markdown
from openfilings.models import (
    SUPPORTED_SOURCE_NAMES,
    CachePruneResult,
    CacheStats,
    Company,
    ExtractionQuality,
    Filing,
    FilingContent,
    FilingFinancials,
    FinancialStatement,
    OcrMode,
    SourceSelection,
)
from openfilings.resources import (
    CompanyResource,
    CompanyResources,
    FilingResource,
    FilingResources,
)
from openfilings.storage.sqlite import SQLiteCache
from openfilings.xbrl import extract_filing_financials
from openfilings.xbrl.cvm_structured import extract_cvm_structured_financials
from openfilings.xbrl.extract import extract_nse_integrated_filing_financials
from openfilings.xbrl.pdf_statements import extract_pdf_ocr_financials
from openfilings.xbrl.sfc_cuif_structured import extract_sfc_cuif_balance_sheet

_Result = TypeVar("_Result")


class OpenFilingsService:
    """Coordinates source retrieval, identity resolution, extraction, and caching."""

    def __init__(
        self,
        cache: SQLiteCache,
        *,
        nsm_source: FcaNsmClient | None = None,
        edinet_source: EdinetClient | None = None,
        esef_sources: tuple[EsefClient, ...] = (),
        cvm_source: CvmClient | None = None,
        sgx_source: SgxClient | None = None,
        market_sources: tuple[PublicMarketClient, ...] = (),
        converter: Callable[[bytes], str] = pdf_to_markdown,
        html_converter: Callable[[bytes], str] = html_to_markdown,
        ocr_converter: OcrConverter = ocr_pdf_to_markdown,
        ocr_available: Callable[[str], bool] = tesseract_available,
        ocr_mode: OcrMode = "auto",
        ocr_language: str = "eng",
        ocr_dpi: int = 200,
        ocr_max_pages: int = 250,
        ocr_executable: str = "tesseract",
        cache_max_mb: int = 512,
        owns_resources: bool = False,
    ) -> None:
        self._nsm = nsm_source
        self._edinet = edinet_source
        self._esef_sources = esef_sources
        self._cvm = cvm_source
        self._sgx = sgx_source
        self._market_sources = market_sources
        self._market_sources_by_name = {
            market_source.source: market_source for market_source in market_sources
        }
        self._cache = cache
        self._pdf_converter = converter
        self._html_converter = html_converter
        self._ocr_converter = ocr_converter
        self._ocr_available = ocr_available
        self._ocr_mode = ocr_mode
        self._ocr_language = ocr_language
        self._ocr_dpi = ocr_dpi
        self._ocr_max_pages = ocr_max_pages
        self._ocr_executable = ocr_executable
        self._cache_max_bytes = cache_max_mb * 1024 * 1024
        self._owns_resources = owns_resources

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> OpenFilingsService:
        settings = settings or Settings.from_env()
        nsm = FcaNsmClient(
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )
        edinet = EdinetClient(
            settings.edinet_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )
        esef_sources = tuple(
            EsefClient(
                market,
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
            )
            for market in ENABLED_ESEF_MARKETS
        )
        cvm = CvmClient(
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )
        sgx = SgxClient(
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )
        market_sources: tuple[PublicMarketClient, ...] = (
            BmvClient(
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
            ),
            NseClient(
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
            ),
            SedarClient(
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
            ),
            SmvClient(
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
            ),
            SfcClient(
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
            ),
            AsxClient(
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
            ),
        )
        cache = SQLiteCache(settings.database_path)
        return cls(
            cache,
            nsm_source=nsm,
            edinet_source=edinet,
            esef_sources=esef_sources,
            cvm_source=cvm,
            sgx_source=sgx,
            market_sources=market_sources,
            ocr_mode=settings.ocr_mode,
            ocr_language=settings.ocr_language,
            ocr_dpi=settings.ocr_dpi,
            ocr_max_pages=settings.ocr_max_pages,
            ocr_executable=settings.ocr_executable,
            cache_max_mb=settings.cache_max_mb,
            owns_resources=True,
        )

    async def __aenter__(self) -> OpenFilingsService:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if not self._owns_resources:
            return
        if self._nsm is not None:
            await self._nsm.aclose()
        if self._edinet is not None:
            await self._edinet.aclose()
        for esef in self._esef_sources:
            await esef.aclose()
        if self._cvm is not None:
            await self._cvm.aclose()
        if self._sgx is not None:
            await self._sgx.aclose()
        for market_source in self._market_sources:
            await market_source.aclose()
        self._cache.close()

    async def search_companies(
        self,
        query: str,
        *,
        limit: int = 10,
        source: SourceSelection = "all",
    ) -> list[Company]:
        selection = self._validate_source(source)
        calls: list[Coroutine[Any, Any, list[Company]]] = []
        if selection in {"all", "fca_nsm"}:
            if self._nsm is None:
                if selection == "fca_nsm":
                    raise ConfigurationError("The FCA NSM source is not configured.")
            else:
                calls.append(self._nsm.search_issuers(query, limit=limit))
        if selection in {"all", "edinet"}:
            if self._edinet is None:
                if selection == "edinet":
                    raise ConfigurationError("The EDINET source is not configured.")
            else:
                calls.append(self._edinet.search_companies(query, limit=limit))
        if selection in {"all", "esef"}:
            if not self._esef_sources and selection == "esef":
                raise ConfigurationError("The ESEF source is not configured.")
            calls.extend(
                esef.search_companies(query, limit=limit) for esef in self._esef_sources
            )
        if selection in {"all", "cvm"}:
            if self._cvm is None:
                if selection == "cvm":
                    raise ConfigurationError("The CVM source is not configured.")
            else:
                calls.append(self._cvm.search_companies(query, limit=limit))
        if selection in {"all", "sgx"}:
            if self._sgx is None:
                if selection == "sgx":
                    raise ConfigurationError("The SGX source is not configured.")
            else:
                calls.append(self._sgx.search_companies(query, limit=limit))
        for source_name, market_source in self._market_sources_by_name.items():
            if selection in {"all", source_name}:
                calls.append(market_source.search_companies(query, limit=limit))

        if selection != "all" and not calls:
            raise ConfigurationError(f"The {selection} source is not configured.")

        results = await self._gather_available(calls)
        companies = self._merge_companies(
            [company for result in results for company in result]
        )[:limit]
        self._cache.put_companies(companies)
        return companies

    async def company(
        self,
        query: str,
        *,
        source: SourceSelection = "all",
        offline: bool = False,
    ) -> CompanyResource:
        """Find one company and bind it to filing operations."""

        companies = await self.companies(
            query,
            limit=10,
            source=source,
            offline=offline,
        )
        if not companies:
            raise CompanyNotFoundError(f"No company matched {query!r}.")
        return companies.find(query) or companies[0]

    async def companies(
        self,
        query: str,
        *,
        limit: int = 10,
        source: SourceSelection = "all",
        offline: bool = False,
    ) -> CompanyResources:
        """Search companies and bind the results to filing operations."""

        selection = self._validate_source(source)
        if offline:
            records = self._cache.search_companies(query, limit=limit)
            if selection != "all":
                records = [record for record in records if selection in record.sources]
        else:
            records = await self.search_companies(
                query,
                limit=limit,
                source=selection,
            )
        return CompanyResources(records, self)

    async def list_filings(
        self,
        company_id: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
        source: SourceSelection = "all",
        nsm_type_codes: list[str] | None = None,
        edinet_lookback_days: int = 120,
    ) -> list[Filing]:
        selection = self._validate_source(source)
        calls: list[Coroutine[Any, Any, list[Filing]]] = []
        cached_market_filings: list[Filing] = []

        if selection in {"all", "fca_nsm"}:
            nsm_identifier = self._nsm_identifier(company_id)
            if self._nsm is None:
                if selection == "fca_nsm":
                    raise ConfigurationError("The FCA NSM source is not configured.")
            elif nsm_identifier:
                # FCA NSM has no generic "category" filter of its own, only
                # disclosure type codes - without this, category="accounts"
                # (the default) is silently a no-op and the latest filing
                # can be any announcement type (a director dealing, an
                # admission notice, ...), not a financial statement.
                effective_type_codes = nsm_type_codes
                if effective_type_codes is None and category == "accounts":
                    effective_type_codes = ["ACS"]
                calls.append(
                    self._nsm.list_filings(
                        nsm_identifier,
                        limit=limit,
                        type_codes=effective_type_codes,
                    )
                )
            elif selection == "fca_nsm":
                raise ConfigurationError(
                    "Search the issuer first so its LEI can be resolved, or pass "
                    "an ID shaped like uk_lei_{LEI}."
                )

        if selection in {"all", "edinet"} and self._is_edinet_company_id(company_id):
            if self._edinet is None:
                raise ConfigurationError("The EDINET source is not configured.")
            calls.append(
                self._list_edinet_filings(
                    company_id,
                    category=category,
                    limit=limit,
                    lookback_days=edinet_lookback_days,
                )
            )
        elif selection == "edinet":
            raise ConfigurationError(
                "Expected a Japanese company ID shaped like jp_E12345."
            )

        if selection in {"all", "esef"} and self._is_esef_company_id(company_id):
            esef = self._esef_for_company_id(company_id)
            if esef is None:
                raise ConfigurationError("The requested ESEF market is not enabled.")
            calls.append(
                esef.list_filings(
                    company_id,
                    category=category,
                    limit=limit,
                )
            )
        elif selection == "esef":
            examples = " or ".join(
                f"{esef.market.id_prefix}_lei_{{LEI}}" for esef in self._esef_sources
            )
            raise ConfigurationError(
                f"Expected an enabled ESEF company ID such as {examples}."
            )

        if selection in {"all", "cvm"} and self._is_cvm_company_id(company_id):
            if self._cvm is None:
                raise ConfigurationError("The CVM source is not configured.")
            calls.append(
                self._cvm.list_filings(
                    company_id,
                    category=category,
                    limit=limit,
                )
            )
        elif selection == "cvm":
            raise ConfigurationError(
                "Expected a Brazilian company ID shaped like br_cvm_{numeric_code}."
            )

        if selection in {"all", "sgx"} and self._is_sgx_company_id(company_id):
            if self._sgx is None:
                raise ConfigurationError("The SGX source is not configured.")
            calls.append(
                self._sgx.list_filings(
                    company_id,
                    category=category,
                    limit=limit,
                )
            )
        elif selection == "sgx":
            raise ConfigurationError(
                "Expected a Singapore company ID shaped like sg_sgx_{IBM_code}."
            )

        market_source = self._market_source_for_company(company_id, selection)
        if market_source is not None:
            cached_market_filings = (
                self._cache.list_filings(
                    company_id,
                    source="sedar",
                    category=category,
                    limit=limit,
                )
                if market_source.source == "sedar"
                else []
            )
            if not cached_market_filings:
                calls.append(
                    market_source.list_filings(
                        company_id,
                        category=category,
                        limit=limit,
                    )
                )
        elif selection in self._market_sources_by_name:
            raise ConfigurationError(
                f"The company ID does not belong to the {selection} source."
            )

        results = await self._gather_available(calls)
        combined = [
            *cached_market_filings,
            *(filing for result in results for filing in result),
        ]
        filings = self._deduplicate_filings(combined)
        filings.sort(
            key=lambda filing: (
                filing.published_at or self._date_sort_value(filing),
                filing.id,
            ),
            reverse=True,
        )
        filings = filings[:limit]
        self._cache.put_filings(filings)
        return filings

    async def filings(
        self,
        company_id: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
        source: SourceSelection = "all",
        nsm_type_codes: list[str] | None = None,
        edinet_lookback_days: int = 120,
        offline: bool = False,
    ) -> FilingResources:
        """Return an immutable, filterable filing collection."""

        selection = self._validate_source(source)
        if offline:
            items = self._cache.list_filings(
                company_id,
                source=None if selection == "all" else selection,
                category=category,
                limit=limit,
            )
        else:
            items = await self.list_filings(
                company_id,
                category=category,
                limit=limit,
                source=selection,
                nsm_type_codes=nsm_type_codes,
                edinet_lookback_days=edinet_lookback_days,
            )
        return FilingResources(items, self)

    async def filing(self, filing_id: str) -> FilingResource:
        """Resolve one filing and bind document and financial operations."""

        filing = self._cache.get_filing(filing_id)
        if filing is None:
            filing = await self._resolve_filing(filing_id)
            self._cache.put_filings([filing])
        return FilingResource(filing, self)

    async def import_sedar_filing(
        self,
        company_id: str,
        *,
        document_url: str,
        title: str,
        filing_date: date,
        period_end: date | None = None,
        filing_type: str = "annual",
        category: str = "accounts",
        document_data: bytes | None = None,
    ) -> FilingResource:
        """Register a user-selected SEDAR+ PDF in the normal filing pipeline."""

        company = self._cache.get_company(company_id)
        if company is None or "sedar" not in company.sources:
            raise CompanyNotFoundError(
                "Search and cache the TSX/TSXV company before importing its filing."
            )
        if not title.strip():
            raise ConfigurationError("The filing title cannot be empty.")
        if not filing_type.strip() or not category.strip():
            raise ConfigurationError("Filing type and category cannot be empty.")

        if document_data is None:
            source = self._market_sources_by_name.get("sedar")
            if source is None:
                raise ConfigurationError("The SEDAR+ source is not configured.")
            document = await source.download_document(document_url)
        else:
            if not document_url.casefold().startswith("file://"):
                document_url = validate_sedar_document_url(document_url)
            document = validate_sedar_pdf(
                document_data,
                source_url=document_url,
            )

        digest = hashlib.sha256(document.data).hexdigest()
        identity = hashlib.sha256(f"{company.id}:{digest}".encode()).hexdigest()
        filing_id = f"ca_sedar_filing_{identity[:24]}"
        filing = Filing(
            id=filing_id,
            company_id=company.id,
            source="sedar",
            source_id=digest,
            title=title.strip(),
            category=category.strip(),
            filing_type=filing_type.strip(),
            filing_date=filing_date,
            period_end=period_end,
            document_id=f"cache:{filing_id}",
            media_type="application/pdf",
            issuer_name=company.name,
            issuer_lei=company.lei,
            pdf_available=True,
            source_url=document.source_url,
        )
        self._cache.put_filings([filing])
        self._cache.put_source_document(filing.id, document)
        self._enforce_cache_limit()
        if self._cache.get_source_document(filing.id) is None:
            self._cache.delete_filing(filing.id)
            raise DocumentUnavailableError(
                "The PDF exceeds the configured cache budget and could not be retained."
            )
        return FilingResource(filing, self)

    async def get_filing_markdown(
        self,
        filing_id: str,
        *,
        refresh: bool = False,
        ocr_mode: OcrMode | None = None,
    ) -> FilingContent:
        if not refresh:
            cached = self._cache.get_content(filing_id)
            if cached is not None:
                return cached

        filing = self._cache.get_filing(filing_id)
        if filing is None:
            filing = await self._resolve_filing(filing_id)
            self._cache.put_filings([filing])

        if not filing.document_id:
            raise DocumentUnavailableError(
                f"Filing {filing.id} does not expose a downloadable document."
            )

        document = await self._download_document(filing)
        digest = hashlib.sha256(document.data).hexdigest()
        duplicate = self._cache.get_content_by_sha256(digest)
        if duplicate is not None and not refresh:
            content = FilingContent(
                filing_id=filing.id,
                markdown=self._add_header(
                    filing,
                    self._markdown_body(duplicate.markdown),
                    quality=duplicate.quality,
                    extraction_method=duplicate.extraction_method,
                ),
                source_url=document.source_url,
                media_type=document.media_type,
                extraction_method=duplicate.extraction_method,
                quality=duplicate.quality,
                sha256=digest,
                from_cache=True,
            )
            self._cache.put_content(content)
            self._enforce_cache_limit()
            return content

        result = await asyncio.to_thread(
            extract_document,
            document,
            pdf_converter=self._pdf_converter,
            html_converter=self._html_converter,
            ocr_converter=self._ocr_converter,
            ocr_available=self._ocr_available,
            ocr_mode=ocr_mode or self._ocr_mode,
            ocr_language=self._ocr_language,
            ocr_dpi=self._ocr_dpi,
            ocr_max_pages=self._ocr_max_pages,
            ocr_executable=self._ocr_executable,
        )
        content = FilingContent(
            filing_id=filing.id,
            markdown=self._add_header(
                filing,
                result.markdown,
                quality=result.quality,
                extraction_method=result.method,
            ),
            source_url=document.source_url,
            media_type=document.media_type,
            extraction_method=result.method,
            quality=result.quality,
            sha256=digest,
        )
        self._cache.put_content(content)
        self._enforce_cache_limit()
        return content

    async def get_filing_document(
        self,
        filing_id: str,
        *,
        refresh: bool = False,
        ocr_mode: OcrMode | None = None,
    ) -> FilingDocument:
        """Return Markdown with EdgarTools-style section navigation and search."""

        content = await self.get_filing_markdown(
            filing_id,
            refresh=refresh,
            ocr_mode=ocr_mode,
        )
        return FilingDocument.from_content(content)

    async def get_filing_financials(
        self,
        filing_id: str,
        *,
        refresh: bool = False,
    ) -> FilingFinancials:
        """Return normalized statements from a tagged or supported PDF filing."""

        if not refresh:
            cached = self._cache.get_financials(filing_id)
            if cached is not None:
                return cached

        filing = self._cache.get_filing(filing_id)
        if filing is None:
            filing = await self._resolve_filing(filing_id)
            self._cache.put_filings([filing])

        if filing.source == "cvm":
            structured = await self._extract_cvm_structured_financials(filing)
            if structured is not None:
                self._cache.put_financials(structured)
                self._enforce_cache_limit()
                return structured

        if filing.source == "nse":
            structured = await self._extract_nse_structured_financials(filing)
            if structured is not None:
                self._cache.put_financials(structured)
                self._enforce_cache_limit()
                return structured

        cuif_balance_sheet = (
            await self._extract_sfc_cuif_balance_sheet(filing)
            if filing.source == "sfc"
            else None
        )

        if not filing.document_id:
            if cuif_balance_sheet is None:
                raise DocumentUnavailableError(
                    f"Filing {filing.id} does not expose a downloadable document."
                )
            financials = self._sfc_cuif_only_financials(filing, cuif_balance_sheet)
            self._cache.put_financials(financials)
            self._enforce_cache_limit()
            return financials

        document = await self._download_document(filing)
        used_cuif_only = False
        try:
            financials = await asyncio.to_thread(
                extract_filing_financials,
                document,
                filing,
            )
        except FinancialsUnavailableError as direct_error:
            try:
                financials = await self._extract_ocr_financials(
                    document,
                    filing,
                    direct_error,
                )
            except FinancialsUnavailableError:
                if cuif_balance_sheet is None:
                    raise
                financials = self._sfc_cuif_only_financials(filing, cuif_balance_sheet)
                used_cuif_only = True

        if cuif_balance_sheet is not None and not used_cuif_only:
            # CUIF's balance-sheet accounts are supervisory stock figures and
            # reconcile exactly; a PDF-derived balance sheet is heuristic, so
            # CUIF replaces it here while the PDF still supplies every other
            # statement family (income statement, cash flow, ...).
            other_statements = tuple(
                statement
                for statement in financials.statements
                if statement.statement_type != "balance_sheet"
            )
            financials = financials.model_copy(
                update={
                    "statements": (*other_statements, cuif_balance_sheet),
                    "extraction_method": (
                        f"{financials.extraction_method}+sfc-cuif-supervisory-data"
                    ),
                }
            )

        self._cache.put_financials(financials)
        self._enforce_cache_limit()
        return financials

    async def _extract_sfc_cuif_balance_sheet(
        self, filing: Filing
    ) -> FinancialStatement | None:
        """Prefer SFC's CUIF supervisory dataset (datos.gov.co) for the
        balance sheet over heuristic PDF parsing - its assets/liabilities/
        equity accounts are stock figures that reconcile exactly. Falls back
        to None (letting the PDF path supply the balance sheet, as before)
        if the entity or cut-off date isn't covered."""

        sfc = self._market_sources_by_name.get("sfc")
        if not isinstance(sfc, SfcClient) or filing.period_end is None:
            return None
        match = re.fullmatch(r"co_sfc_(\d{1,4})_(\d{1,6})", filing.company_id, re.I)
        if match is None:
            return None
        try:
            rows = await sfc.cuif_balance_sheet_rows(
                match.group(1), match.group(2), filing.period_end
            )
        except SourceError:
            return None
        if rows is None:
            return None
        return extract_sfc_cuif_balance_sheet(rows, filing)

    @staticmethod
    def _sfc_cuif_only_financials(
        filing: Filing, balance_sheet: FinancialStatement
    ) -> FilingFinancials:
        return FilingFinancials(
            filing_id=filing.id,
            company_id=filing.company_id,
            source_url=CUIF_DATASET_URL,
            extraction_method="sfc-cuif-supervisory-data",
            statements=(balance_sheet,),
            fact_count=len(balance_sheet.line_items),
            taxonomy_namespaces=("sfc-cuif",),
            sha256=hashlib.sha256(
                f"{filing.id}:{filing.period_end}".encode()
            ).hexdigest(),
        )

    async def _extract_cvm_structured_financials(
        self, filing: Filing
    ) -> FilingFinancials | None:
        """Prefer CVM's Open Data DFP/ITR dataset over the PDF filing - it's
        already tagged to a standardized chart of accounts, so it doesn't
        need heuristic PDF parsing at all. Falls back to None (letting the
        caller use the PDF path) if the company or year isn't covered."""

        if self._cvm is None or filing.period_end is None:
            return None
        match = re.fullmatch(r"br_cvm_(\d{1,6})", filing.company_id, re.IGNORECASE)
        if match is None:
            return None
        cd_cvm = match.group(1).zfill(6)
        dataset = "itr" if filing.filing_type == "interim" else "dfp"
        try:
            archive = await self._cvm.structured_archive(
                dataset, filing.period_end.year
            )
        except SourceError:
            return None
        if archive is None:
            return None
        digest = hashlib.sha256(archive).hexdigest()
        try:
            return await asyncio.to_thread(
                extract_cvm_structured_financials,
                archive,
                filing,
                cd_cvm=cd_cvm,
                source_url=CvmClient.structured_archive_url(
                    dataset, filing.period_end.year
                ),
                sha256=digest,
            )
        except FinancialsUnavailableError:
            return None

    async def _extract_nse_structured_financials(
        self, filing: Filing
    ) -> FilingFinancials | None:
        """Prefer NSE's Integrated Filing - Financials XBRL over the annual-
        report PDF - since April 2025 it's the only format SEBI Regulation
        33 financial results are filed in (PDF submission was
        discontinued), and its taxonomy already matches the standard IFRS
        concepts this codebase recognizes elsewhere. Falls back to None
        (letting the caller use the PDF annual report) if no audited filing
        covers this exact fiscal-year-end."""

        nse = self._market_sources_by_name.get("nse")
        if not isinstance(nse, NseClient) or filing.period_end is None:
            return None
        match = re.fullmatch(r"in_nse_([A-Za-z0-9&-]{1,30})", filing.company_id, re.I)
        if match is None:
            return None
        try:
            result = await nse.integrated_filing_xbrl(
                match.group(1), filing.period_end
            )
        except SourceError:
            return None
        if result is None:
            return None
        data, source_url = result
        try:
            return await asyncio.to_thread(
                extract_nse_integrated_filing_financials,
                data,
                filing,
                source_url=source_url,
                sha256=hashlib.sha256(data).hexdigest(),
            )
        except FinancialsUnavailableError:
            return None

    async def _extract_ocr_financials(
        self,
        document: SourceDocument,
        filing: Filing,
        direct_error: FinancialsUnavailableError,
    ) -> FilingFinancials:
        is_pdf = (
            document.media_type.casefold() == "application/pdf"
            or document.data[:4] == b"%PDF"
        )
        if not is_pdf or self._ocr_mode == "never":
            raise direct_error
        if not self._ocr_available(self._ocr_executable):
            raise FinancialsUnavailableError(
                f"{direct_error} Install Tesseract or set OPENFILINGS_OCR_MODE=never."
            ) from direct_error
        try:
            markdown = await asyncio.to_thread(
                self._ocr_converter,
                document.data,
                language=self._ocr_language,
                dpi=self._ocr_dpi,
                max_pages=self._ocr_max_pages,
                executable=self._ocr_executable,
            )
        except ExtractionError as exc:
            raise FinancialsUnavailableError(
                f"The filing could not be OCR-processed for financial tables: {exc}"
            ) from exc
        digest = hashlib.sha256(document.data).hexdigest()
        return await asyncio.to_thread(
            extract_pdf_ocr_financials,
            markdown,
            filing,
            source_url=document.source_url,
            sha256=digest,
        )

    def cache_stats(self) -> CacheStats:
        return self._cache.stats()

    def prune_cache(self, *, max_mb: int) -> CachePruneResult:
        if max_mb < 0:
            raise ConfigurationError("Cache size cannot be negative.")
        before = self._cache.stats()
        max_bytes = max_mb * 1024 * 1024
        removed_sources = self._cache.prune_source_documents(
            max(
                0,
                max_bytes
                - before.compressed_content_bytes
                - before.compressed_financial_bytes,
            )
        )
        interim = self._cache.stats()
        removed = self._cache.prune_content(
            max(
                0,
                max_bytes
                - interim.compressed_source_bytes
                - interim.compressed_financial_bytes,
            )
        )
        interim = self._cache.stats()
        removed_financials = self._cache.prune_financials(
            max(
                0,
                max_bytes
                - interim.compressed_source_bytes
                - interim.compressed_content_bytes,
            )
        )
        self._cache.vacuum()
        return CachePruneResult(
            removed_documents=removed,
            removed_source_documents=removed_sources,
            removed_financial_reports=removed_financials,
            before=before,
            after=self._cache.stats(),
        )

    async def _resolve_filing(self, filing_id: str) -> Filing:
        if filing_id.casefold().startswith("jp_edinet_"):
            raise FilingNotFoundError(
                "EDINET filing metadata is not cached. List the Japanese company's "
                "filings before fetching the document."
            )
        if filing_id.lower().startswith("uk_nsm_"):
            if self._nsm is None:
                raise ConfigurationError("The FCA NSM source is not configured.")
            disclosure_id = filing_id[len("uk_nsm_") :]
            if not disclosure_id:
                raise FilingNotFoundError(
                    "Expected an FCA filing ID shaped like uk_nsm_{disclosure_id}."
                )
            return await self._nsm.get_filing(disclosure_id)
        esef = self._esef_for_filing_id(filing_id)
        if esef is not None:
            return await esef.get_filing(filing_id)
        if filing_id.casefold().startswith("br_cvm_"):
            raise FilingNotFoundError(
                "CVM filing metadata is not cached. List the Brazilian company's "
                "filings before fetching the document."
            )
        if filing_id.casefold().startswith("sg_sgx_"):
            raise FilingNotFoundError(
                "SGX filing metadata is not cached. List the Singapore company's "
                "filings before fetching the document."
            )
        market_source = next(
            (
                source
                for source in self._market_sources
                if source.matches_filing_id(filing_id)
            ),
            None,
        )
        if market_source is not None:
            raise FilingNotFoundError(
                f"{market_source.source.upper()} filing metadata is not cached. "
                "List the company's filings before fetching the document."
            )

        raise FilingNotFoundError(f"Unsupported filing ID: {filing_id}.")

    async def _download_document(self, filing: Filing) -> SourceDocument:
        cached = self._cache.get_source_document(filing.id)
        if cached is not None:
            return cached
        if filing.source == "edinet":
            if self._edinet is None:
                raise ConfigurationError("The EDINET source is not configured.")
            return await self._edinet.download_document(filing.document_id or "")
        if filing.source == "fca_nsm":
            if self._nsm is None:
                raise ConfigurationError("The FCA NSM source is not configured.")
            return await self._nsm.download_document(filing.document_id or "")
        if filing.source == "esef":
            esef = self._esef_for_company_id(filing.company_id)
            if esef is None:
                raise ConfigurationError("The filing's ESEF market is not enabled.")
            return await esef.download_document(filing.document_id or "")
        if filing.source == "cvm":
            if self._cvm is None:
                raise ConfigurationError("The CVM source is not configured.")
            return await self._cvm.download_document(filing.document_id or "")
        if filing.source == "sgx":
            if self._sgx is None:
                raise ConfigurationError("The SGX source is not configured.")
            return await self._sgx.download_document(filing.document_id or "")
        market_source = self._market_sources_by_name.get(filing.source)
        if market_source is not None:
            return await market_source.download_document(filing.document_id or "")
        raise DocumentUnavailableError(f"Unsupported filing source: {filing.source}.")

    async def _list_edinet_filings(
        self,
        company_id: str,
        *,
        category: str | None,
        limit: int,
        lookback_days: int,
    ) -> list[Filing]:
        if self._edinet is None:
            raise ConfigurationError("The EDINET source is not configured.")
        if not 1 <= lookback_days <= 3_661:
            raise ConfigurationError("EDINET history days must be between 1 and 3661.")
        code = self._edinet.normalize_edinet_code(company_id)
        state_key = f"edinet-history:{code}:{category or 'all'}:{lookback_days}:{limit}"
        state = self._cache.get_market_state(state_key)
        if state:
            try:
                cached_at = datetime.fromisoformat(state)
            except ValueError:
                cached_at = datetime.min.replace(tzinfo=UTC)
            if datetime.now(UTC) - cached_at < timedelta(hours=6):
                return self._cache.list_filings(
                    company_id,
                    source="edinet",
                    category=category,
                    limit=limit,
                )

        filings = await self._edinet.list_filings(
            code,
            category=category,
            limit=limit,
            lookback_days=lookback_days,
        )
        self._cache.put_filings(filings)
        self._cache.put_market_state(state_key, datetime.now(UTC).isoformat())
        return filings

    def _nsm_identifier(self, company_id: str) -> str | None:
        if company_id.lower().startswith("uk_lei_"):
            return company_id
        company = self._cache.get_company(company_id)
        if company is None:
            return None
        if company.market != "UK":
            return None
        if company.lei:
            return company.lei
        return company.name if "fca_nsm" in company.sources else None

    def _enforce_cache_limit(self) -> None:
        stats = self._cache.stats()
        self._cache.prune_source_documents(
            max(
                0,
                self._cache_max_bytes
                - stats.compressed_content_bytes
                - stats.compressed_financial_bytes,
            )
        )
        stats = self._cache.stats()
        self._cache.prune_content(
            max(
                0,
                self._cache_max_bytes
                - stats.compressed_source_bytes
                - stats.compressed_financial_bytes,
            )
        )
        stats = self._cache.stats()
        self._cache.prune_financials(
            max(
                0,
                self._cache_max_bytes
                - stats.compressed_source_bytes
                - stats.compressed_content_bytes,
            )
        )

    @staticmethod
    async def _gather_available(
        calls: list[Coroutine[Any, Any, list[_Result]]],
    ) -> list[list[_Result]]:
        if not calls:
            return []
        results = await asyncio.gather(*calls, return_exceptions=True)
        successes = [result for result in results if isinstance(result, list)]
        if successes:
            return successes
        first_error = next(
            (result for result in results if isinstance(result, Exception)), None
        )
        if first_error:
            raise first_error
        return []

    @classmethod
    def _merge_companies(cls, companies: list[Company]) -> list[Company]:
        merged: dict[str, Company] = {}
        order: list[str] = []
        for company in companies:
            key = cls._company_key(company.market, company.name)
            existing = merged.get(key)
            if existing is None:
                merged[key] = company
                order.append(key)
                continue
            sources = tuple(dict.fromkeys((*existing.sources, *company.sources)))
            merged[key] = existing.model_copy(
                update={
                    "sources": sources,
                    "lei": existing.lei or company.lei,
                }
            )
        return [merged[key] for key in order]

    @classmethod
    def _deduplicate_filings(cls, filings: list[Filing]) -> list[Filing]:
        seen: set[tuple[str, ...]] = set()
        unique: list[Filing] = []
        for filing in filings:
            url_key = ("url", filing.source_url.casefold())
            metadata_key = (
                "metadata",
                filing.filing_date.isoformat(),
                cls._normalize_text(filing.title),
            )
            if url_key in seen or metadata_key in seen:
                continue
            seen.update((url_key, metadata_key))
            unique.append(filing)
        return unique

    @classmethod
    def _company_key(cls, market: str, name: str) -> str:
        if market == "JP":
            normalized = "".join(
                character for character in name.casefold() if character.isalnum()
            )
            return f"jp:{normalized}"
        normalized = cls._normalize_text(name)
        suffixes = (" public limited company", " limited", " ltd", " plc")
        for suffix in suffixes:
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)].strip()
                break
        return f"{market.casefold()}:{normalized}"

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())

    @staticmethod
    def _date_sort_value(filing: Filing) -> datetime:
        return datetime.combine(filing.filing_date, datetime.min.time(), tzinfo=UTC)

    @staticmethod
    def _validate_source(source: str) -> SourceSelection:
        normalized = source.strip().casefold().replace("-", "_")
        supported = {"all", *SUPPORTED_SOURCE_NAMES}
        if normalized not in supported:
            source_names = (
                "all, fca_nsm, edinet, esef, cvm, sgx, bmv, nse, sedar, smv, "
                "sfc, asx"
            )
            raise ConfigurationError(f"Source must be one of: {source_names}.")
        return cast(SourceSelection, normalized)

    def _market_source_for_company(
        self,
        company_id: str,
        selection: SourceSelection,
    ) -> PublicMarketClient | None:
        if selection != "all":
            source = self._market_sources_by_name.get(selection)
            return source if source and source.matches_company_id(company_id) else None
        return next(
            (
                source
                for source in self._market_sources
                if source.matches_company_id(company_id)
            ),
            None,
        )

    @staticmethod
    def _is_edinet_company_id(company_id: str) -> bool:
        return company_id.casefold().startswith("jp_e")

    @staticmethod
    def _is_cvm_company_id(company_id: str) -> bool:
        return company_id.casefold().startswith("br_cvm_")

    @staticmethod
    def _is_sgx_company_id(company_id: str) -> bool:
        return company_id.casefold().startswith("sg_sgx_")

    def _is_esef_company_id(self, company_id: str) -> bool:
        return self._esef_for_company_id(company_id) is not None

    def _esef_for_company_id(self, company_id: str) -> EsefClient | None:
        return next(
            (
                esef
                for esef in self._esef_sources
                if esef.matches_company_id(company_id)
            ),
            None,
        )

    def _esef_for_filing_id(self, filing_id: str) -> EsefClient | None:
        return next(
            (esef for esef in self._esef_sources if esef.matches_filing_id(filing_id)),
            None,
        )

    @staticmethod
    def _markdown_body(markdown: str) -> str:
        marker = "\n---\n\n"
        return markdown.split(marker, 1)[1] if marker in markdown else markdown

    @staticmethod
    def _add_header(
        filing: Filing,
        markdown: str,
        *,
        quality: ExtractionQuality | None = None,
        extraction_method: str | None = None,
    ) -> str:
        body = markdown.strip()
        issuer = filing.issuer_name or filing.company_id
        quality_line = (
            f"- Extraction quality: {quality.status} ({quality.score}/100)\n"
            if quality
            else ""
        )
        method_line = (
            f"- Extraction method: `{extraction_method}`\n" if extraction_method else ""
        )
        return (
            f"# {filing.title}\n\n"
            f"- Issuer: {issuer}\n"
            f"- Company ID: `{filing.company_id}`\n"
            f"- Filed: {filing.filing_date.isoformat()}\n"
            f"- Filing type: `{filing.filing_type}`\n"
            f"- Source system: `{filing.source}`\n"
            f"{method_line}"
            f"{quality_line}"
            f"- Source: {filing.source_url}\n\n"
            f"---\n\n{body}\n"
        )
