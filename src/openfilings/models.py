"""Normalized public models shared by the CLI, cache, adapters, and MCP."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


SourceName = Literal["companies_house", "fca_nsm"]
QualityStatus = Literal["good", "degraded", "unusable"]
OcrMode = Literal["auto", "never", "always"]
StatementType = Literal[
    "income_statement",
    "balance_sheet",
    "cash_flow_statement",
    "comprehensive_income",
    "changes_in_equity",
]


class ExtractionQuality(DomainModel):
    score: int = Field(default=50, ge=0, le=100)
    status: QualityStatus = "degraded"
    character_count: int = Field(default=0, ge=0)
    page_count: int | None = Field(default=None, ge=0)
    characters_per_page: float | None = Field(default=None, ge=0)
    alphanumeric_ratio: float = Field(default=0.0, ge=0, le=1)
    replacement_character_ratio: float = Field(default=0.0, ge=0, le=1)
    warnings: tuple[str, ...] = ("quality_not_recorded",)


class IssuerReference(DomainModel):
    name: str
    lei: str | None = None


class Company(DomainModel):
    id: str
    source_id: str
    name: str
    sources: tuple[SourceName, ...] = ("companies_house",)
    lei: str | None = None
    market: Literal["UK"] = "UK"
    country_code: Literal["GB"] = "GB"
    status: str | None = None
    company_type: str | None = None
    address: str | None = None
    source_url: str


class Filing(DomainModel):
    id: str
    company_id: str
    source: SourceName = "companies_house"
    source_id: str
    title: str
    category: str
    filing_type: str
    filing_date: date
    published_at: datetime | None = None
    description: str | None = None
    pages: int | None = None
    document_id: str | None = None
    document_metadata_url: str | None = None
    media_type: str | None = None
    issuer_name: str | None = None
    issuer_lei: str | None = None
    related_issuers: tuple[IssuerReference, ...] = ()
    source_url: str

    @property
    def has_document(self) -> bool:
        return self.document_id is not None


class FilingContent(DomainModel):
    filing_id: str
    markdown: str
    source_url: str
    media_type: str = "application/pdf"
    extraction_method: str = "pymupdf4llm"
    quality: ExtractionQuality = Field(default_factory=ExtractionQuality)
    sha256: str = Field(min_length=64, max_length=64)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    from_cache: bool = False


class ReportingPeriod(DomainModel):
    """A duration or instant context attached to one or more XBRL facts."""

    id: str
    start_date: date | None = None
    end_date: date
    kind: Literal["instant", "duration"]
    fiscal_period: str | None = None


class FinancialValue(DomainModel):
    """One normalized numeric fact with its original XBRL provenance."""

    period: ReportingPeriod
    value: Decimal
    unit: str | None = None
    decimals: str | None = None
    dimensions: tuple[tuple[str, str], ...] = ()


class FinancialLineItem(DomainModel):
    """A standardized line item backed by a source taxonomy concept."""

    code: str
    name: str
    concept: str
    values: tuple[FinancialValue, ...]


class FinancialStatement(DomainModel):
    """A compact, cross-period financial statement."""

    statement_type: StatementType
    title: str
    currency: str | None = None
    line_items: tuple[FinancialLineItem, ...]


class FilingFinancials(DomainModel):
    """Structured statements extracted from a tagged filing."""

    filing_id: str
    company_id: str
    source_url: str
    extraction_method: str = "inline-xbrl-stream"
    statements: tuple[FinancialStatement, ...]
    fact_count: int = Field(ge=0)
    taxonomy_namespaces: tuple[str, ...] = ()
    sha256: str = Field(min_length=64, max_length=64)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    from_cache: bool = False


class CacheStats(DomainModel):
    companies: int = Field(ge=0)
    filings: int = Field(ge=0)
    documents: int = Field(ge=0)
    financial_reports: int = Field(default=0, ge=0)
    compressed_content_bytes: int = Field(ge=0)
    compressed_financial_bytes: int = Field(default=0, ge=0)
    database_bytes: int = Field(ge=0)


class CachePruneResult(DomainModel):
    removed_documents: int = Field(ge=0)
    removed_financial_reports: int = Field(default=0, ge=0)
    before: CacheStats
    after: CacheStats
