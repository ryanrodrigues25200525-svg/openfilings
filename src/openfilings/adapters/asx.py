"""Official ASX listed-issuer discovery for Australian companies.

Australia is discovery-only, for the same reason Canada is: no keyless path
to a company's filing history exists.

ASX publishes its listed-company directory as a free CSV, so company search
is fully supported. Filing retrieval is not, and measurement rather than
assumption is why:

- ASIC's lodged financial reports are a paid-download product
  (``connectonline.asic.gov.au``).
- ASX's public announcements feed lists every issuer's disclosures but
  accepts no issuer filter - ``issuer_code``, ``asx_code`` and friends are
  all silently ignored - so a single company's history can only be recovered
  by paging the global feed backward and discarding ~99.9% of each response.
  Measured against the live endpoint, one uncached page costs 18-20 seconds
  and covers about six days, so reaching a company's last annual report runs
  to roughly twelve minutes and four years of history to over an hour.
- The per-company endpoint on ``asx.api.markitdigital.com`` returns a hard
  cap of five items regardless of any count/date parameter, and those are
  dominated by routine notices (issued capital, security-holder details),
  so periodic financial reports are usually absent from it entirely.

Both paths were verified against the live endpoints before Australia was
demoted to discovery-only. Use the company's own investor-relations site or
a commercial ASIC/ASX data product for Australian financial reports.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from openfilings._version import __version__
from openfilings.adapters._common import RetryingClient, ranked_matches
from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.models import Company, Filing, SourceName

LISTED_COMPANIES_URL = (
    "https://asx.api.markitdigital.com/asx-research/1.0/companies/directory/file"
    "?access_subscription=free&csv=true"
)
COMPANY_PAGE_URL = "https://www.asx.com.au/markets/company/{code}"
ANNOUNCEMENTS_URL = "https://www.asx.com.au/markets/trade-our-cash-market/announcements"

_CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,6}$")
_MAX_REGISTRY_BYTES = 2 * 1024 * 1024


class AsxClient(RetryingClient):
    """Search ASX-listed issuers. Filing retrieval is not keyless-available."""

    source: SourceName = "asx"

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__(
            "ASX",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            headers={
                "User-Agent": f"openfilings/{__version__}",
                "Accept": "application/json",
            },
            client=client,
        )
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
        code = self._code(company_id)
        raise SourceError(
            "ASX filing retrieval has no keyless source: ASIC's lodged financial "
            "reports are a paid product, and ASX's public announcements feed "
            "accepts no issuer filter, so one company's history costs minutes of "
            "full-feed paging per report. Browse this issuer's announcements at "
            f"{ANNOUNCEMENTS_URL} (ASX code {code}), or use a commercial "
            "ASIC/ASX data product."
        )

    async def download_document(self, document_id: str) -> SourceDocument:
        raise DocumentUnavailableError(
            "ASX documents are not retrievable through OpenFilings; Australia is "
            f"company-discovery-only. See {ANNOUNCEMENTS_URL}."
        )

    def matches_company_id(self, value: str) -> bool:
        return value.casefold().startswith("au_asx_")

    def matches_filing_id(self, value: str) -> bool:
        return value.casefold().startswith("au_asx_filing_")

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
                if "company name" in line.casefold() and "asx code" in line.casefold()
            ),
            None,
        )
        if header_index is None:
            raise SourceError("ASX returned an invalid listed-companies CSV.")
        reader = csv.DictReader(lines[header_index:])
        companies: list[Company] = []
        for raw_row in reader:
            # ponytail: ASX has shipped both column orders and "GICS"/"GICs" casing
            row = {
                (key or "").strip().casefold(): value for key, value in raw_row.items()
            }
            name = (row.get("company name") or "").strip()
            code = (row.get("asx code") or "").strip().upper()
            industry = (row.get("gics industry group") or "").strip()
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
    def _code(value: str) -> str:
        clean = value.strip().upper()
        if clean.casefold().startswith("au_asx_"):
            clean = clean[len("au_asx_") :]
        if not _CODE_PATTERN.fullmatch(clean):
            raise SourceError("Expected an ASX company ID shaped like au_asx_BHP.")
        return clean
