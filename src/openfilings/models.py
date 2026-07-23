"""Normalized public models shared by the CLI, cache, adapters, and MCP."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


SourceName = Literal[
    "fca_nsm",
    "edinet",
    "esef",
    "cvm",
    "sgx",
    "bmv",
    "nse",
    "sedar",
    "smv",
    "sfc",
]
SourceSelection = Literal[
    "all",
    "fca_nsm",
    "edinet",
    "esef",
    "cvm",
    "sgx",
    "bmv",
    "nse",
    "sedar",
    "smv",
    "sfc",
]
SUPPORTED_SOURCE_NAMES = frozenset(
    {
        "fca_nsm",
        "edinet",
        "esef",
        "cvm",
        "sgx",
        "bmv",
        "nse",
        "sedar",
        "smv",
        "sfc",
    }
)
MarketCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
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
    sources: tuple[SourceName, ...] = ("fca_nsm",)
    lei: str | None = None
    market: MarketCode = "UK"
    country_code: CountryCode = "GB"
    ticker: str | None = None
    local_code: str | None = None
    english_name: str | None = None
    status: str | None = None
    company_type: str | None = None
    address: str | None = None
    source_url: str


class Filing(DomainModel):
    id: str
    company_id: str
    source: SourceName = "fca_nsm"
    source_id: str
    title: str
    category: str
    filing_type: str
    filing_date: date
    published_at: datetime | None = None
    period_start: date | None = None
    period_end: date | None = None
    description: str | None = None
    pages: int | None = None
    document_id: str | None = None
    document_metadata_url: str | None = None
    media_type: str | None = None
    issuer_name: str | None = None
    issuer_lei: str | None = None
    related_issuers: tuple[IssuerReference, ...] = ()
    language: str = "en"
    xbrl_available: bool = False
    pdf_available: bool = False
    csv_available: bool = False
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

    @property
    def label(self) -> str:
        prefix = f"{self.fiscal_period} " if self.fiscal_period else ""
        return f"{prefix}{self.end_date.isoformat()}"


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

    @property
    def periods(self) -> tuple[ReportingPeriod, ...]:
        periods: dict[str, ReportingPeriod] = {}
        for item in self.line_items:
            for value in item.values:
                periods.setdefault(value.period.label, value.period)
        return tuple(periods.values())

    def to_records(self) -> tuple[dict[str, object], ...]:
        labels = [period.label for period in self.periods]
        records: list[dict[str, object]] = []
        for item in self.line_items:
            values = {value.period.label: value.value for value in item.values}
            records.append(
                {
                    "code": item.code,
                    "name": item.name,
                    "concept": item.concept,
                    **{label: values.get(label) for label in labels},
                }
            )
        return tuple(records)

    def to_markdown(self) -> str:
        labels = [period.label for period in self.periods]
        heading = f"## {self.title}"
        currency = f" ({self.currency})" if self.currency else ""
        header = ["Line item", *labels]
        divider = ["---", *("---:" for _ in labels)]
        rows = [
            [
                str(record["name"]),
                *(_markdown_value(record[label]) for label in labels),
            ]
            for record in self.to_records()
        ]
        table = "\n".join(_markdown_row(row) for row in [header, divider, *rows])
        return f"{heading}{currency}\n\n{table}\n"

    def to_dataframe(self) -> Any:
        """Return a pandas DataFrame when the optional dependency is installed."""

        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError(
                "Install openfilings[dataframe] to use to_dataframe()."
            ) from exc
        return pd.DataFrame(self.to_records())


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

    def statement(self, statement_type: StatementType) -> FinancialStatement | None:
        return next(
            (
                statement
                for statement in self.statements
                if statement.statement_type == statement_type
            ),
            None,
        )

    def income_statement(self) -> FinancialStatement | None:
        return self.statement("income_statement")

    def balance_sheet(self) -> FinancialStatement | None:
        return self.statement("balance_sheet")

    def cash_flow_statement(self) -> FinancialStatement | None:
        return self.statement("cash_flow_statement")

    def comprehensive_income(self) -> FinancialStatement | None:
        return self.statement("comprehensive_income")

    def changes_in_equity(self) -> FinancialStatement | None:
        return self.statement("changes_in_equity")

    def to_markdown(self) -> str:
        return "\n".join(statement.to_markdown() for statement in self.statements)


class CacheStats(DomainModel):
    companies: int = Field(ge=0)
    filings: int = Field(ge=0)
    documents: int = Field(ge=0)
    source_documents: int = Field(default=0, ge=0)
    financial_reports: int = Field(default=0, ge=0)
    compressed_content_bytes: int = Field(ge=0)
    compressed_source_bytes: int = Field(default=0, ge=0)
    compressed_financial_bytes: int = Field(default=0, ge=0)
    database_bytes: int = Field(ge=0)


class CachePruneResult(DomainModel):
    removed_documents: int = Field(ge=0)
    removed_source_documents: int = Field(default=0, ge=0)
    removed_financial_reports: int = Field(default=0, ge=0)
    before: CacheStats
    after: CacheStats


def _markdown_row(values: list[str]) -> str:
    return "| " + " | ".join(value.replace("|", "\\|") for value in values) + " |"


def _markdown_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)
