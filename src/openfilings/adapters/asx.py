"""Official ASX public-announcements client for Australian listed issuers.

ASX has no free, unauthenticated API scoped to a single company's filing
history, and ASIC's lodged financial reports are a paid-download product
(``connectonline.asic.gov.au``). The only free, keyless path is ASX's own
public announcements feed, which lists every issuer's disclosures but exposes
no company filter, so a company's history is recovered by paging the global
feed backward in time and keeping the rows for the requested issuer code.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from openfilings.adapters._common import RetryingClient, ranked_matches
from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing, SourceName

LISTED_COMPANIES_URL = "https://www.asx.com.au/asx/research/ASXListedCompanies.csv"
ANNOUNCEMENT_LIST_URL = "https://www.asx.com.au/asx/1/announcement/list"
COMPANY_PAGE_URL = "https://www.asx.com.au/markets/company/{code}"
ANNOUNCEMENTS_HOST = "announcements.asx.com.au"

_CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,6}$")
_ANNOUNCEMENT_ID_PATTERN = re.compile(r"^\d{6,10}$")
_PDF_PATH_PATTERN = re.compile(r"^/asxpdf/\d{8}/pdf/[a-z0-9]+\.pdf$", re.IGNORECASE)
_PAGE_SIZE = 2000
_MAX_LIST_BYTES = 4 * 1024 * 1024
_MAX_REGISTRY_BYTES = 2 * 1024 * 1024

# Headline text ASX issuers use for the standardised Appendix forms that carry
# financial statements. Anything else is treated as non-financial disclosure.
_ANNUAL_TERMS = ("annual report", "preliminary final report", "appendix 4e")
_HALF_YEAR_TERMS = ("half yearly report", "half year report", "appendix 4d")
_QUARTERLY_TERMS = (
    "quarterly activities report",
    "quarterly cashflow report",
    "appendix 4c",
    "appendix 5b",
)


class AsxClient(RetryingClient):
    """Search ASX-listed issuers and retrieve their financial-report PDFs."""

    source: SourceName = "asx"

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        history_years: int = 4,
        max_pages: int = 40,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(
            "ASX",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            headers={"User-Agent": "openfilings/0.21", "Accept": "application/json"},
            client=client,
        )
        self._history_years = max(1, min(history_years, 10))
        self._max_pages = max(1, min(max_pages, 200))
        self._now = now
        self._companies: tuple[Company, ...] | None = None

    async def search_companies(self, query: str, *, limit: int = 10) -> list[Company]:
        companies = await self._company_registry()
        records = [
            ((company.source_id, company.name), company) for company in companies
        ]
        return ranked_matches(query.removeprefix("au_asx_"), records, limit=limit)

    async def list_filings(
        self,
        company_id: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
    ) -> list[Filing]:
        if category and category.casefold() != "accounts":
            return []
        code = self._code(company_id)
        company = next(
            (item for item in await self._company_registry() if item.source_id == code),
            None,
        )
        if company is None:
            raise SourceError(f"ASX code {code} is not a current listed issuer.")

        cutoff = self._now() - timedelta(days=365 * self._history_years)
        end_ms = int(self._now().timestamp() * 1000)
        matches: dict[str, Filing] = {}
        for _ in range(self._max_pages):
            response = await self._request(
                "GET",
                ANNOUNCEMENT_LIST_URL,
                params={"end_date": end_ms, "page_size": _PAGE_SIZE},
            )
            if len(response.content) > _MAX_LIST_BYTES:
                raise SourceError("The ASX announcement feed is unexpectedly large.")
            rows = self._announcement_rows(response)
            if not rows:
                break

            oldest: datetime | None = None
            for row in rows:
                released = self._parse_release(row.get("document_release_date"))
                if released is not None and (oldest is None or released < oldest):
                    oldest = released
                if str(row.get("issuer_code", "")).strip().upper() != code:
                    continue
                filing = self._filing_from_row(row, company, released)
                if filing is not None:
                    matches[filing.id] = filing

            if oldest is None or oldest < cutoff or len(matches) >= limit:
                break
            end_ms = int(oldest.timestamp() * 1000) - 1

        filings = sorted(
            matches.values(), key=lambda filing: filing.filing_date, reverse=True
        )
        return filings[: max(1, limit)]

    async def download_document(self, document_id: str) -> SourceDocument:
        source_url = self.document_url(document_id)
        response = await self._request("GET", source_url)
        data = response.content
        if not data:
            raise DocumentUnavailableError("The ASX announcement PDF was empty.")
        if len(data) > MAX_TAGGED_DOCUMENT_BYTES:
            raise DocumentUnavailableError("The ASX announcement PDF exceeds 150 MB.")
        if not data.startswith(b"%PDF"):
            raise DocumentUnavailableError(
                "ASX returned an invalid response instead of the announcement PDF."
            )
        return SourceDocument(
            data=data, media_type="application/pdf", source_url=source_url
        )

    def matches_company_id(self, value: str) -> bool:
        return value.casefold().startswith("au_asx_")

    def matches_filing_id(self, value: str) -> bool:
        return value.casefold().startswith("au_asx_filing_")

    @staticmethod
    def document_url(value: str) -> str:
        url = value.strip()
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != ANNOUNCEMENTS_HOST
            or _PDF_PATH_PATTERN.fullmatch(parsed.path) is None
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise DocumentUnavailableError("Unsafe ASX announcement PDF URL.")
        return url

    async def _company_registry(self) -> tuple[Company, ...]:
        if self._companies is not None:
            return self._companies
        response = await self._request("GET", LISTED_COMPANIES_URL)
        if len(response.content) > _MAX_REGISTRY_BYTES:
            raise SourceError("The ASX listed-companies CSV is unexpectedly large.")
        text = response.content.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        header_index = next(
            (
                index
                for index, line in enumerate(lines)
                if line.strip().casefold().startswith("company name,")
            ),
            None,
        )
        if header_index is None:
            raise SourceError("ASX returned an invalid listed-companies CSV.")
        reader = csv.DictReader(lines[header_index:])
        companies: list[Company] = []
        for row in reader:
            name = (row.get("Company name") or "").strip()
            code = (row.get("ASX code") or "").strip().upper()
            industry = (row.get("GICS industry group") or "").strip()
            if not name or not _CODE_PATTERN.fullmatch(code):
                continue
            companies.append(
                Company(
                    id=f"au_asx_{code}",
                    source_id=code,
                    name=name,
                    sources=("asx",),
                    market="AU",
                    country_code="AU",
                    ticker=f"{code}.AX",
                    local_code=code,
                    status="ASX listed issuer",
                    company_type=industry or None,
                    source_url=COMPANY_PAGE_URL.format(code=code),
                )
            )
        if not companies:
            raise SourceError("The ASX listed-companies CSV contained no issuers.")
        self._companies = tuple(companies)
        return self._companies

    @staticmethod
    def _announcement_rows(response: httpx.Response) -> list[dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError("ASX returned invalid announcement-list JSON.") from exc
        if not isinstance(payload, dict):
            raise SourceError("ASX returned an invalid announcement-list response.")
        rows = payload.get("announcement_data", [])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SourceError("ASX returned invalid announcement records.")
        return rows

    @classmethod
    def _filing_from_row(
        cls,
        row: dict[str, Any],
        company: Company,
        released: datetime | None,
    ) -> Filing | None:
        header = str(row.get("header", "")).strip()
        classification = cls._classify(header)
        if classification is None or released is None:
            return None
        announcement_id = str(row.get("id", "")).strip()
        if not _ANNOUNCEMENT_ID_PATTERN.fullmatch(announcement_id):
            return None
        try:
            source_url = cls.document_url(str(row.get("url", "")))
        except DocumentUnavailableError:
            return None
        pages = row.get("number_of_pages")
        filing_type = classification
        return Filing(
            id=f"au_asx_filing_{announcement_id}",
            company_id=company.id,
            source="asx",
            source_id=announcement_id,
            title=header,
            category="accounts",
            filing_type=filing_type,
            filing_date=released.date(),
            published_at=released.astimezone(UTC),
            pages=pages if isinstance(pages, int) and pages > 0 else None,
            document_id=source_url,
            media_type="application/pdf",
            issuer_name=str(row.get("issuer_full_name", "")).strip() or company.name,
            language="en",
            pdf_available=True,
            source_url=source_url,
        )

    @staticmethod
    def _classify(header: str) -> str | None:
        lowered = header.casefold()
        if any(term in lowered for term in _ANNUAL_TERMS):
            return "annual"
        if any(term in lowered for term in _HALF_YEAR_TERMS):
            return "half_year"
        if any(term in lowered for term in _QUARTERLY_TERMS):
            return "quarterly"
        return None

    @staticmethod
    def _parse_release(value: Any) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    @staticmethod
    def _code(value: str) -> str:
        clean = value.strip().upper()
        if clean.casefold().startswith("au_asx_"):
            clean = clean[len("au_asx_") :]
        if not _CODE_PATTERN.fullmatch(clean):
            raise SourceError("Expected an ASX company ID shaped like au_asx_BHP.")
        return clean
