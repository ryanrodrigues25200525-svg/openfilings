"""Official National Stock Exchange of India public-filings client."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import UTC, date, datetime
from urllib.parse import urlparse

import httpx

from openfilings.adapters._common import RetryingClient, ranked_matches
from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing, SourceName

LANDING_URL = (
    "https://www.nseindia.com/companies-listing/corporate-filings-annual-reports"
)
COMPANY_REGISTRY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
ANNUAL_REPORTS_URL = "https://www.nseindia.com/api/annual-reports"

_ALLOWED_SERIES = {"EQ", "BE", "BZ"}
_ARCHIVE_HOST = "nsearchives.nseindia.com"
_MAX_REGISTRY_BYTES = 5 * 1024 * 1024


class NseClient(RetryingClient):
    """Search NSE equity issuers and retrieve their annual reports."""

    source: SourceName = "nse"

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            "NSE",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/126 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            client=client,
        )
        self._companies: tuple[Company, ...] | None = None
        self._session_ready = False

    async def search_companies(self, query: str, *, limit: int = 10) -> list[Company]:
        companies = await self._company_registry()
        records = [
            (
                (
                    company.ticker or "",
                    company.source_id,
                    company.name,
                ),
                company,
            )
            for company in companies
        ]
        return ranked_matches(query.removeprefix("in_nse_"), records, limit=limit)

    async def list_filings(
        self,
        company_id: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
    ) -> list[Filing]:
        symbol = self._symbol(company_id)
        company = next(
            (item for item in await self._company_registry() if item.ticker == symbol),
            None,
        )
        if company is None:
            raise SourceError(f"NSE symbol {symbol} is not an active listed equity.")
        await self._prepare_session()
        response = await self._request(
            "GET",
            ANNUAL_REPORTS_URL,
            params={"index": "equities", "symbol": symbol},
            headers={
                "Referer": LANDING_URL,
                "Accept": "application/json,text/plain,*/*",
            },
        )
        try:
            rows = response.json().get("data", [])
        except (ValueError, AttributeError) as exc:
            raise SourceError(
                "NSE returned an invalid annual-report response."
            ) from exc
        if not isinstance(rows, list):
            raise SourceError("NSE returned an invalid annual-report response.")
        filings = [
            filing
            for row in rows
            if (filing := self._filing_from_row(company, row)) is not None
        ]
        filings.sort(key=lambda filing: filing.published_at, reverse=True)
        return filings[: max(1, limit)]

    async def download_document(self, document_id: str) -> SourceDocument:
        source_url = self.document_url(document_id)
        await self._prepare_session()
        response = await self._request(
            "GET",
            source_url,
            headers={"Referer": LANDING_URL},
        )
        data = response.content
        if not data:
            raise DocumentUnavailableError("The NSE annual report was empty.")
        if len(data) > MAX_TAGGED_DOCUMENT_BYTES:
            raise DocumentUnavailableError("The NSE report exceeds the 150 MB limit.")
        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        if data.startswith(b"PK\x03\x04"):
            extracted = self._single_pdf_from_zip(data)
            if extracted is not None:
                data = extracted
                media_type = "application/pdf"
            else:
                media_type = "application/zip"
        elif data.startswith(b"%PDF"):
            media_type = "application/pdf"
        elif media_type.startswith("text/html"):
            raise DocumentUnavailableError(
                "NSE returned an HTML error page instead of an annual report."
            )
        return SourceDocument(data=data, media_type=media_type, source_url=source_url)

    def matches_company_id(self, value: str) -> bool:
        return value.casefold().startswith("in_nse_")

    def matches_filing_id(self, value: str) -> bool:
        return value.casefold().startswith("in_nse_filing_")

    @staticmethod
    def document_url(value: str) -> str:
        url = value.strip()
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != _ARCHIVE_HOST
            or not parsed.path.startswith("/annual_reports/")
            or ".." in parsed.path
            or not parsed.path.casefold().endswith((".pdf", ".zip"))
        ):
            raise DocumentUnavailableError("Unsafe NSE annual-report URL.")
        return url

    async def _prepare_session(self) -> None:
        if not self._session_ready:
            await self._request("GET", LANDING_URL)
            self._session_ready = True

    async def _company_registry(self) -> tuple[Company, ...]:
        if self._companies is not None:
            return self._companies
        await self._prepare_session()
        response = await self._request(
            "GET",
            COMPANY_REGISTRY_URL,
            headers={"Referer": LANDING_URL, "Accept": "text/csv,*/*"},
        )
        if len(response.content) > _MAX_REGISTRY_BYTES:
            raise SourceError("The NSE equity registry is unexpectedly large.")
        try:
            reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
            rows = list(reader)
        except (UnicodeDecodeError, csv.Error) as exc:
            raise SourceError("NSE returned an invalid equity registry.") from exc
        companies = [
            company for row in rows if (company := self._company_from_row(row))
        ]
        self._companies = tuple(companies)
        return self._companies

    @staticmethod
    def _company_from_row(row: dict[str, str]) -> Company | None:
        symbol = row.get("SYMBOL", "").strip().upper()
        name = row.get("NAME OF COMPANY", "").strip()
        isin = row.get(" ISIN NUMBER", row.get("ISIN NUMBER", "")).strip().upper()
        series = row.get(" SERIES", row.get("SERIES", "")).strip().upper()
        if (
            not symbol
            or not name
            or not isin.startswith("INE")
            or series not in _ALLOWED_SERIES
        ):
            return None
        return Company(
            id=f"in_nse_{symbol}",
            source_id=isin,
            name=name,
            sources=("nse",),
            market="IN",
            country_code="IN",
            ticker=symbol,
            local_code=symbol,
            status="active listed equity issuer",
            company_type=f"NSE {series} equity",
            source_url=(f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}"),
        )

    def _filing_from_row(self, company: Company, row: object) -> Filing | None:
        if not isinstance(row, dict):
            return None
        source_url = str(row.get("fileName", "")).strip()
        try:
            source_url = self.document_url(source_url)
        except DocumentUnavailableError:
            return None
        start_year = str(row.get("fromYr", "")).strip()
        end_year = str(row.get("toYr", "")).strip()
        if not (start_year.isdigit() and end_year.isdigit()):
            return None
        source_id_match = re.search(r"/AR_(\d+)_", source_url)
        if source_id_match is None:
            return None
        source_id = source_id_match.group(1)
        timestamp = self._parse_timestamp(str(row.get("broadcast_dttm", "")))
        if timestamp is None:
            timestamp = datetime(int(end_year), 8, 1, tzinfo=UTC)
        media_type = (
            "application/pdf" if source_url.endswith(".pdf") else "application/zip"
        )
        return Filing(
            id=f"in_nse_filing_{source_id}",
            company_id=company.id,
            source="nse",
            source_id=source_id,
            title=f"Annual Report {start_year}-{end_year}",
            category="accounts",
            filing_type="annual",
            filing_date=timestamp.date(),
            published_at=timestamp,
            period_start=date(int(start_year), 4, 1),
            period_end=date(int(end_year), 3, 31),
            document_id=source_url,
            media_type=media_type,
            issuer_name=company.name,
            language="en",
            pdf_available=media_type == "application/pdf",
            source_url=source_url,
        )

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        try:
            return datetime.strptime(value, "%d-%b-%Y %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            return None

    @staticmethod
    def _single_pdf_from_zip(data: bytes) -> bytes | None:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                pdfs = [
                    member
                    for member in archive.infolist()
                    if not member.is_dir()
                    and member.filename.casefold().endswith(".pdf")
                    and member.file_size <= MAX_TAGGED_DOCUMENT_BYTES
                ]
                return (
                    archive.read(max(pdfs, key=lambda item: item.file_size))
                    if pdfs
                    else None
                )
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise DocumentUnavailableError(
                "The NSE report archive is invalid."
            ) from exc

    @staticmethod
    def _symbol(value: str) -> str:
        clean = value.strip().upper()
        if clean.casefold().startswith("in_nse_"):
            clean = clean[len("in_nse_") :]
        if not re.fullmatch(r"[A-Z0-9&-]{1,30}", clean):
            raise SourceError(
                "Expected an Indian company ID shaped like in_nse_RELIANCE."
            )
        return clean
