"""Shared source-adapter value objects."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol

from openfilings.models import Company, Filing, SourceName


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """Downloaded source bytes plus the provenance needed for extraction."""

    data: bytes
    media_type: str
    source_url: str
    profile: str | None = None


class PublicMarketClient(Protocol):
    """Common contract for independently discoverable public-market sources."""

    source: SourceName

    def search_companies(
        self, query: str, *, limit: int = 10
    ) -> Awaitable[list[Company]]: ...

    def list_filings(
        self,
        company_id: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
    ) -> Awaitable[list[Filing]]: ...

    def download_document(self, document_id: str) -> Awaitable[SourceDocument]: ...

    def matches_company_id(self, value: str) -> bool: ...

    def matches_filing_id(self, value: str) -> bool: ...

    def aclose(self) -> Awaitable[None]: ...
