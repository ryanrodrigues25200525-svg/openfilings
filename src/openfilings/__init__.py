"""OpenFilings: regulator-neutral access to public corporate filings."""

from openfilings.domain import FilingDocument, Filings, SectionSearchResult
from openfilings.models import (
    Company,
    Filing,
    FilingContent,
    FilingFinancials,
    FinancialStatement,
)
from openfilings.resources import (
    CompanyResource,
    CompanyResources,
    FilingResource,
    FilingResources,
    PrefetchResult,
)
from openfilings.service import OpenFilingsService

OpenFilings = OpenFilingsService

__all__ = [
    "Company",
    "CompanyResource",
    "CompanyResources",
    "Filing",
    "FilingContent",
    "FilingDocument",
    "FilingFinancials",
    "FilingResource",
    "FilingResources",
    "Filings",
    "FinancialStatement",
    "OpenFilings",
    "OpenFilingsService",
    "PrefetchResult",
    "SectionSearchResult",
]
