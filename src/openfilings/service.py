"""Application service shared by command-line and MCP interfaces."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, NoReturn, TypeVar, cast

from openfilings.adapters.base import SourceDocument
from openfilings.adapters.companies_house import CompaniesHouseClient
from openfilings.adapters.edinet import EdinetClient
from openfilings.adapters.fca_nsm import FcaNsmClient
from openfilings.config import Settings
from openfilings.domain import FilingDocument, Filings
from openfilings.exceptions import (
    ConfigurationError,
    DocumentUnavailableError,
    FilingNotFoundError,
)
from openfilings.extraction.document import OcrConverter, extract_document
from openfilings.extraction.html import html_to_markdown
from openfilings.extraction.ocr import ocr_pdf_to_markdown, tesseract_available
from openfilings.extraction.pdf import pdf_to_markdown
from openfilings.models import (
    CachePruneResult,
    CacheStats,
    Company,
    ExtractionQuality,
    Filing,
    FilingContent,
    FilingFinancials,
    OcrMode,
)
from openfilings.storage.sqlite import SQLiteCache
from openfilings.xbrl import extract_filing_financials

SourceSelection = Literal["all", "companies_house", "fca_nsm", "edinet"]
_Result = TypeVar("_Result")


class OpenFilingsService:
    """Coordinates source retrieval, identity resolution, extraction, and caching."""

    def __init__(
        self,
        source: CompaniesHouseClient | None,
        cache: SQLiteCache,
        *,
        nsm_source: FcaNsmClient | None = None,
        edinet_source: EdinetClient | None = None,
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
        self._companies_house = source
        self._nsm = nsm_source
        self._edinet = edinet_source
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
        companies_house = None
        if settings.companies_house_api_key:
            companies_house = CompaniesHouseClient(
                settings.companies_house_api_key,
                timeout_seconds=settings.request_timeout_seconds,
                max_retries=settings.max_retries,
            )
        nsm = FcaNsmClient(
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )
        edinet = EdinetClient(
            settings.edinet_api_key,
            timeout_seconds=settings.request_timeout_seconds,
            max_retries=settings.max_retries,
        )
        cache = SQLiteCache(settings.database_path)
        return cls(
            companies_house,
            cache,
            nsm_source=nsm,
            edinet_source=edinet,
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
        if self._companies_house is not None:
            await self._companies_house.aclose()
        if self._nsm is not None:
            await self._nsm.aclose()
        if self._edinet is not None:
            await self._edinet.aclose()
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
        if selection in {"all", "companies_house"}:
            if self._companies_house is None:
                if selection == "companies_house":
                    self._raise_missing_companies_house_key()
            else:
                calls.append(self._companies_house.search_companies(query, limit=limit))
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

        results = await self._gather_available(calls)
        companies = self._merge_companies(
            [company for result in results for company in result]
        )[:limit]
        self._cache.put_companies(companies)
        return companies

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

        if (
            selection in {"all", "companies_house"}
            and not self._is_nsm_company_id(company_id)
            and not self._is_edinet_company_id(company_id)
        ):
            if self._companies_house is None:
                if selection == "companies_house":
                    self._raise_missing_companies_house_key()
            else:
                company_number = self._companies_house.normalize_company_number(
                    company_id
                )
                calls.append(
                    self._companies_house.list_filings(
                        company_number,
                        category=category,
                        limit=limit,
                    )
                )

        if selection in {"all", "fca_nsm"}:
            nsm_identifier = self._nsm_identifier(company_id)
            if self._nsm is None:
                if selection == "fca_nsm":
                    raise ConfigurationError("The FCA NSM source is not configured.")
            elif nsm_identifier:
                calls.append(
                    self._nsm.list_filings(
                        nsm_identifier,
                        limit=limit,
                        type_codes=nsm_type_codes,
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

        results = await self._gather_available(calls)
        combined = [filing for result in results for filing in result]
        if not self._is_nsm_company_id(company_id):
            combined = [
                filing.model_copy(update={"company_id": company_id})
                if filing.source == "fca_nsm"
                else filing
                for filing in combined
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
    ) -> Filings:
        """Return an immutable, filterable filing collection."""

        items = await self.list_filings(
            company_id,
            category=category,
            limit=limit,
            source=source,
            nsm_type_codes=nsm_type_codes,
            edinet_lookback_days=edinet_lookback_days,
        )
        return Filings(items)

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
        """Return normalized statements from a UK/ESEF tagged filing."""

        if not refresh:
            cached = self._cache.get_financials(filing_id)
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
        financials = await asyncio.to_thread(
            extract_filing_financials,
            document,
            filing,
        )
        self._cache.put_financials(financials)
        self._enforce_cache_limit()
        return financials

    def cache_stats(self) -> CacheStats:
        return self._cache.stats()

    def prune_cache(self, *, max_mb: int) -> CachePruneResult:
        if max_mb < 0:
            raise ConfigurationError("Cache size cannot be negative.")
        before = self._cache.stats()
        max_bytes = max_mb * 1024 * 1024
        removed = self._cache.prune_content(
            max(0, max_bytes - before.compressed_financial_bytes)
        )
        interim = self._cache.stats()
        removed_financials = self._cache.prune_financials(
            max(0, max_bytes - interim.compressed_content_bytes)
        )
        self._cache.vacuum()
        return CachePruneResult(
            removed_documents=removed,
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

        if self._companies_house is None:
            self._raise_missing_companies_house_key()
        company_number, transaction_id = self._parse_companies_house_filing_id(
            filing_id
        )
        return await self._companies_house.get_filing(company_number, transaction_id)

    async def _download_document(self, filing: Filing) -> SourceDocument:
        if filing.source == "edinet":
            if self._edinet is None:
                raise ConfigurationError("The EDINET source is not configured.")
            return await self._edinet.download_document(filing.document_id or "")
        if filing.source == "fca_nsm":
            if self._nsm is None:
                raise ConfigurationError("The FCA NSM source is not configured.")
            return await self._nsm.download_document(filing.document_id or "")
        if self._companies_house is None:
            self._raise_missing_companies_house_key()
        return await self._companies_house.download_document(filing.document_id or "")

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
        if company.lei:
            return company.lei
        return company.name if "fca_nsm" in company.sources else None

    def _enforce_cache_limit(self) -> None:
        stats = self._cache.stats()
        self._cache.prune_content(
            max(0, self._cache_max_bytes - stats.compressed_financial_bytes)
        )
        stats = self._cache.stats()
        self._cache.prune_financials(
            max(0, self._cache_max_bytes - stats.compressed_content_bytes)
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
            preferred = existing if "companies_house" in existing.sources else company
            merged[key] = preferred.model_copy(
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
        if normalized not in {"all", "companies_house", "fca_nsm", "edinet"}:
            raise ConfigurationError(
                "Source must be one of: all, companies_house, fca_nsm, edinet."
            )
        return cast(SourceSelection, normalized)

    @staticmethod
    def _is_nsm_company_id(company_id: str) -> bool:
        lowered = company_id.lower()
        return lowered.startswith(("uk_lei_", "uk_nsm_issuer_"))

    @staticmethod
    def _is_edinet_company_id(company_id: str) -> bool:
        return company_id.casefold().startswith("jp_e")

    @staticmethod
    def _parse_companies_house_filing_id(filing_id: str) -> tuple[str, str]:
        parts = filing_id.split("_", 2)
        if len(parts) != 3 or parts[0].lower() != "uk" or not all(parts[1:]):
            raise FilingNotFoundError(
                "Expected a filing ID shaped like uk_{company_number}_{transaction_id}."
            )
        return parts[1], parts[2]

    @staticmethod
    def _markdown_body(markdown: str) -> str:
        marker = "\n---\n\n"
        return markdown.split(marker, 1)[1] if marker in markdown else markdown

    @staticmethod
    def _raise_missing_companies_house_key() -> NoReturn:
        raise ConfigurationError(
            "COMPANIES_HOUSE_API_KEY is required for Companies House requests. "
            "Use --source fca-nsm for the free FCA-only path."
        )

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
