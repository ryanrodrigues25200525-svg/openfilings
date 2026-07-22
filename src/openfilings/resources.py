"""Service-bound resources for an EdgarTools-style public API."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from openfilings.domain import (
    DocumentSection,
    FilingDocument,
    Filings,
    SectionSearchResult,
)
from openfilings.models import (
    Company,
    Filing,
    FilingContent,
    FilingFinancials,
    OcrMode,
    SourceSelection,
)

if TYPE_CHECKING:
    from openfilings.service import OpenFilingsService


@dataclass(frozen=True, slots=True)
class CompanyResource:
    """A company record bound to filing operations."""

    record: Company
    _service: OpenFilingsService = field(repr=False, compare=False)

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def name(self) -> str:
        return self.record.name

    @property
    def lei(self) -> str | None:
        return self.record.lei

    @property
    def market(self) -> str:
        return self.record.market

    @property
    def country_code(self) -> str:
        return self.record.country_code

    @property
    def ticker(self) -> str | None:
        return self.record.ticker

    @property
    def sources(self) -> tuple[str, ...]:
        return self.record.sources

    @property
    def source_url(self) -> str:
        return self.record.source_url

    async def get_filings(
        self,
        *,
        category: str | None = "accounts",
        limit: int = 25,
        source: SourceSelection = "all",
        nsm_type_codes: list[str] | None = None,
        edinet_lookback_days: int = 120,
        offline: bool = False,
    ) -> FilingResources:
        return await self._service.filings(
            self.id,
            category=category,
            limit=limit,
            source=source,
            nsm_type_codes=nsm_type_codes,
            edinet_lookback_days=edinet_lookback_days,
            offline=offline,
        )


class CompanyResources(Sequence[CompanyResource]):
    """Immutable company search results bound to filing operations."""

    def __init__(
        self,
        companies: Sequence[Company],
        service: OpenFilingsService,
    ) -> None:
        self._service = service
        self._items = tuple(CompanyResource(company, service) for company in companies)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[CompanyResource]:
        return iter(self._items)

    def __getitem__(self, index: int | slice) -> CompanyResource | CompanyResources:
        selected = self._items[index]
        if isinstance(index, slice):
            return CompanyResources([item.record for item in selected], self._service)
        return selected

    def head(self, count: int = 5) -> CompanyResources:
        if count < 0:
            raise ValueError("count cannot be negative")
        records = [item.record for item in self._items[:count]]
        return CompanyResources(records, self._service)

    @property
    def records(self) -> tuple[Company, ...]:
        return tuple(item.record for item in self._items)

    def filter(
        self,
        *,
        market: str | None = None,
        country_code: str | None = None,
        source: str | None = None,
    ) -> CompanyResources:
        records = [
            item.record
            for item in self._items
            if (market is None or item.market.casefold() == market.casefold())
            and (
                country_code is None
                or item.country_code.casefold() == country_code.casefold()
            )
            and (source is None or source in item.sources)
        ]
        return CompanyResources(records, self._service)

    def find(self, value: str) -> CompanyResource | None:
        wanted = value.strip().casefold()
        return next(
            (
                item
                for item in self._items
                if wanted
                in {
                    item.id.casefold(),
                    item.name.casefold(),
                    (item.lei or "").casefold(),
                    (item.record.ticker or "").casefold(),
                    (item.record.local_code or "").casefold(),
                }
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class FilingResource:
    """A filing record with bound document and financial operations."""

    record: Filing
    _service: OpenFilingsService = field(repr=False, compare=False)

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def title(self) -> str:
        return self.record.title

    @property
    def filing_type(self) -> str:
        return self.record.filing_type

    @property
    def filing_date(self) -> date:
        return self.record.filing_date

    @property
    def company_id(self) -> str:
        return self.record.company_id

    @property
    def source(self) -> str:
        return self.record.source

    @property
    def period_end(self) -> date | None:
        return self.record.period_end

    @property
    def source_url(self) -> str:
        return self.record.source_url

    @property
    def language(self) -> str:
        return self.record.language

    @property
    def has_document(self) -> bool:
        return self.record.has_document

    @property
    def xbrl_available(self) -> bool:
        return self.record.xbrl_available

    async def content(
        self, *, refresh: bool = False, ocr_mode: OcrMode | None = None
    ) -> FilingContent:
        return await self._service.get_filing_markdown(
            self.id,
            refresh=refresh,
            ocr_mode=ocr_mode,
        )

    async def markdown(
        self, *, refresh: bool = False, ocr_mode: OcrMode | None = None
    ) -> str:
        return (await self.content(refresh=refresh, ocr_mode=ocr_mode)).markdown

    async def document(
        self, *, refresh: bool = False, ocr_mode: OcrMode | None = None
    ) -> FilingDocument:
        return await self._service.get_filing_document(
            self.id,
            refresh=refresh,
            ocr_mode=ocr_mode,
        )

    async def obj(
        self, *, refresh: bool = False, ocr_mode: OcrMode | None = None
    ) -> FilingDocument:
        return await self.document(refresh=refresh, ocr_mode=ocr_mode)

    async def sections(self) -> tuple[DocumentSection, ...]:
        return (await self.document()).sections

    async def search(
        self, query: str, *, limit: int = 20
    ) -> tuple[SectionSearchResult, ...]:
        return (await self.document()).ranked_search(query, limit=limit)

    async def financials(self, *, refresh: bool = False) -> FilingFinancials:
        return await self._service.get_filing_financials(self.id, refresh=refresh)

    async def xbrl(self, *, refresh: bool = False) -> FilingFinancials:
        return await self.financials(refresh=refresh)


@dataclass(frozen=True, slots=True)
class PrefetchFailure:
    filing_id: str
    operation: str
    error: str


@dataclass(frozen=True, slots=True)
class PrefetchResult:
    requested: int
    documents_cached: int
    financials_cached: int
    failures: tuple[PrefetchFailure, ...]


class FilingResources(Sequence[FilingResource]):
    """Immutable bound filing collection with filtering and bulk prefetch."""

    def __init__(
        self,
        filings: Sequence[Filing],
        service: OpenFilingsService,
    ) -> None:
        self._service = service
        self._items = tuple(FilingResource(filing, service) for filing in filings)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[FilingResource]:
        return iter(self._items)

    def __getitem__(self, index: int | slice) -> FilingResource | FilingResources:
        selected = self._items[index]
        if isinstance(index, slice):
            return FilingResources([item.record for item in selected], self._service)
        return selected

    @property
    def records(self) -> Filings:
        return Filings([item.record for item in self._items])

    @property
    def empty(self) -> bool:
        return not self._items

    def head(self, count: int = 5) -> FilingResources:
        return FilingResources(tuple(self.records.head(count)), self._service)

    def latest(self, count: int = 1) -> FilingResource | FilingResources | None:
        selected = self.records.latest(count)
        if isinstance(selected, Filing):
            return FilingResource(selected, self._service)
        if selected is None:
            return None
        return FilingResources(tuple(selected), self._service)

    def filter(
        self,
        *,
        source: str | None = None,
        filing_type: str | Sequence[str] | None = None,
        category: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        has_document: bool | None = None,
    ) -> FilingResources:
        selected = self.records.filter(
            source=source,
            filing_type=filing_type,
            category=category,
            date_from=date_from,
            date_to=date_to,
            has_document=has_document,
        )
        return FilingResources(tuple(selected), self._service)

    async def prefetch(
        self,
        *,
        documents: bool = True,
        financials: bool = False,
        concurrency: int = 3,
    ) -> PrefetchResult:
        if not documents and not financials:
            raise ValueError("documents or financials must be enabled")
        if concurrency < 1:
            raise ValueError("concurrency must be at least one")

        semaphore = asyncio.Semaphore(concurrency)

        async def load(item: FilingResource) -> tuple[int, int, list[PrefetchFailure]]:
            async with semaphore:
                return await _prefetch_resource(
                    item,
                    documents=documents,
                    financials=financials,
                )

        results = await asyncio.gather(*(load(item) for item in self._items))
        return PrefetchResult(
            requested=len(self),
            documents_cached=sum(result[0] for result in results),
            financials_cached=sum(result[1] for result in results),
            failures=tuple(failure for result in results for failure in result[2]),
        )


async def _prefetch_resource(
    filing: FilingResource,
    *,
    documents: bool,
    financials: bool,
) -> tuple[int, int, list[PrefetchFailure]]:
    documents_cached = 0
    financials_cached = 0
    failures: list[PrefetchFailure] = []
    if documents:
        try:
            await filing.content()
            documents_cached = 1
        except Exception as exc:
            failures.append(PrefetchFailure(filing.id, "document", str(exc)))
    if financials:
        try:
            await filing.financials()
            financials_cached = 1
        except Exception as exc:
            failures.append(PrefetchFailure(filing.id, "financials", str(exc)))
    return documents_cached, financials_cached, failures
