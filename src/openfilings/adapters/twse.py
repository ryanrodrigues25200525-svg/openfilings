"""Official TWSE and MOPS client for Taiwan-listed company reports."""

from __future__ import annotations

import asyncio
import io
import re
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx

from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing

COMPANY_API_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
DOCUMENT_SERVER_URL = "https://doc.twse.com.tw/server-java/t57sb01"
COMPANY_PROFILE_URL = "https://mops.twse.com.tw/mops/web/t05st03"

_TAIPEI = ZoneInfo("Asia/Taipei")
_COMPANY_CODE_PATTERN = re.compile(r"^[A-Z0-9]{4,10}$", re.IGNORECASE)
_ANNUAL_REPORT_PATTERN = re.compile(
    r"^(?P<period>\d{4})_(?P<code>[A-Z0-9]{4,10})_"
    r"(?P<upload>\d{8})F(?:(?P<english>E)|0)4\.(?P<extension>pdf|zip)$",
    re.IGNORECASE,
)
_FILING_ID_PATTERN = re.compile(
    r"^tw_mops_(?P<stem>\d{4}_[A-Z0-9]{4,10}_\d{8}F(?:E|0)4)$",
    re.IGNORECASE,
)
_DOWNLOAD_LINK_PATTERN = re.compile(
    r"readfile2?\([\"']F[\"'],[\"'](?P<code>[A-Z0-9]{4,10})[\"'],"
    r"[\"'](?P<filename>[^\"']+)[\"']\)",
    re.IGNORECASE,
)
_MAX_COMPANY_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_LIST_RESPONSE_BYTES = 5 * 1024 * 1024
_PDF_HANDOFF_PATTERN = re.compile(
    r"href=[\"'](?P<path>/pdf/[A-Z0-9_.-]+\.pdf)[\"']", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class _TableCell:
    text: str
    href: str | None = None


class _ReportTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[_TableCell]] = []
        self._row: list[_TableCell] | None = None
        self._text: list[str] | None = None
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._text = []
            self._href = None
        elif tag == "a" and self._text is not None:
            self._href = dict(attrs).get("href")

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._row is not None and self._text is not None:
            text = " ".join("".join(self._text).replace("\xa0", " ").split())
            self._row.append(_TableCell(text=text, href=self._href))
            self._text = None
            self._href = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


class TwseClient:
    """Search TWSE-listed issuers and retrieve MOPS annual-report PDFs."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        history_years: int = 5,
        client: httpx.AsyncClient | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._max_retries = max_retries
        self._history_years = max(1, min(history_years, 10))
        self._today = today
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "User-Agent": "openfilings/0.19",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            },
        )
        self._companies: tuple[Company, ...] | None = None
        self._search_aliases: dict[str, tuple[str, ...]] = {}

    async def __aenter__(self) -> TwseClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search_companies(self, query: str, *, limit: int = 10) -> list[Company]:
        clean_query = query.strip()
        if not clean_query:
            return []
        companies = await self._company_registry()
        normalized = self._normalize_search(clean_query.removeprefix("tw_twse_"))
        ranked: list[tuple[int, str, Company]] = []
        for company in companies:
            fields = (
                company.local_code or "",
                company.ticker or "",
                company.name,
                company.english_name or "",
                *self._search_aliases.get(company.id, ()),
            )
            normalized_fields = tuple(self._normalize_search(value) for value in fields)
            if not any(normalized in value for value in normalized_fields):
                continue
            if normalized in normalized_fields[:2]:
                rank = 0
            elif any(value.startswith(normalized) for value in normalized_fields[2:]):
                rank = 1
            else:
                rank = 2
            ranked.append((rank, company.name, company))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in ranked[: max(1, limit)]]

    async def list_filings(
        self,
        company_id_or_code: str,
        *,
        limit: int = 25,
        category: str | None = "accounts",
    ) -> list[Filing]:
        if category and category.casefold() != "accounts":
            return []
        code = self.normalize_company_code(company_id_or_code)
        company = await self._company_by_code(code)
        filings: dict[str, Filing] = {}
        current_roc_year = self._today().year - 1911
        for roc_year in range(
            current_roc_year, current_roc_year - self._history_years, -1
        ):
            rows = await self._report_rows(code, roc_year)
            for row in rows:
                filing = self._filing_from_row(row, company=company)
                if filing is not None:
                    filings[filing.id] = filing
            if len(filings) >= max(1, limit):
                break
        return sorted(
            filings.values(),
            key=lambda item: (
                item.published_at or self._date_time(item.filing_date),
                item.id,
            ),
            reverse=True,
        )[: max(1, limit)]

    async def get_filing(self, filing_id: str) -> Filing:
        match = _FILING_ID_PATTERN.fullmatch(filing_id.strip())
        if match is None:
            raise SourceError(
                "Expected a TWSE filing ID shaped like "
                "tw_mops_{year}_{company}_{upload_date}F04."
            )
        filename = f"{match.group('stem')}.pdf"
        details = self._annual_report_details(filename)
        if details is None:
            raise SourceError(f"TWSE filing {filing_id} has invalid metadata.")
        company = await self._company_by_code(details.code)
        return self._filing_from_details(details, company=company)

    async def download_document(self, document_url: str) -> SourceDocument:
        source_url = self.document_url(document_url)
        response = await self._request("GET", source_url)
        data = response.content
        if not data:
            raise DocumentUnavailableError("The TWSE filing document was empty.")
        if len(data) > MAX_TAGGED_DOCUMENT_BYTES:
            raise DocumentUnavailableError("The TWSE filing exceeds the 150 MB limit.")
        if not data.startswith((b"%PDF", b"PK\x03\x04")):
            handoff_url = self._pdf_handoff_url(data)
            if handoff_url is None:
                raise DocumentUnavailableError(
                    "TWSE returned an invalid response instead of the filing PDF."
                )
            response = await self._request("GET", handoff_url)
            data = response.content
            if len(data) > MAX_TAGGED_DOCUMENT_BYTES:
                raise DocumentUnavailableError(
                    "The TWSE filing exceeds the 150 MB limit."
                )
        if data.startswith(b"PK\x03\x04"):
            data = self._pdf_from_archive(data)
        if not data.startswith(b"%PDF"):
            raise DocumentUnavailableError(
                "TWSE returned an invalid response instead of the filing PDF."
            )
        return SourceDocument(
            data=data,
            media_type="application/pdf",
            source_url=source_url,
        )

    @staticmethod
    def _pdf_handoff_url(data: bytes) -> str | None:
        if len(data) > 1024 * 1024:
            return None
        html = data.decode("big5", errors="replace")
        match = _PDF_HANDOFF_PATTERN.search(html)
        if match is None:
            return None
        url = urljoin("https://doc.twse.com.tw", match.group("path"))
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "doc.twse.com.tw"
            or not re.fullmatch(r"/pdf/[A-Z0-9_.-]+\.pdf", parsed.path, re.IGNORECASE)
            or parsed.query
        ):
            return None
        return url

    def matches_company_id(self, value: str) -> bool:
        return value.casefold().startswith("tw_twse_")

    def matches_filing_id(self, value: str) -> bool:
        return value.casefold().startswith("tw_mops_")

    @staticmethod
    def normalize_company_code(value: str) -> str:
        clean_value = value.strip()
        if clean_value.casefold().startswith("tw_twse_"):
            clean_value = clean_value[len("tw_twse_") :]
        if not _COMPANY_CODE_PATTERN.fullmatch(clean_value):
            raise SourceError(
                "Expected a TWSE company ID shaped like tw_twse_{stock_code}."
            )
        return clean_value.upper()

    @staticmethod
    def document_url(value: str) -> str:
        url = value.strip()
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        filename = query.get("filename", [""])[0]
        details = TwseClient._annual_report_details(filename)
        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != "doc.twse.com.tw"
            or parsed.path != "/server-java/t57sb01"
            or query.get("step") != ["9"]
            or query.get("kind") != ["F"]
            or query.get("co_id", [""])[0].upper() != (details.code if details else "")
        ):
            raise DocumentUnavailableError("Unsafe TWSE document URL.")
        return url

    async def _company_registry(self) -> tuple[Company, ...]:
        if self._companies is not None:
            return self._companies
        response = await self._request("GET", COMPANY_API_URL)
        if len(response.content) > _MAX_COMPANY_RESPONSE_BYTES:
            raise SourceError("The TWSE company response is unexpectedly large.")
        try:
            rows = response.json()
        except ValueError as exc:
            raise SourceError("TWSE returned invalid company JSON.") from exc
        if not isinstance(rows, list):
            raise SourceError("TWSE returned unexpected company data.")
        companies: dict[str, Company] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            company = self._company_from_row(row)
            if company is not None:
                companies[company.id] = company
                self._search_aliases[company.id] = (
                    str(row.get("公司簡稱") or "").strip(),
                    str(row.get("英文簡稱") or "").strip(),
                )
        self._companies = tuple(companies.values())
        return self._companies

    async def _company_by_code(self, code: str) -> Company:
        company = next(
            (
                company
                for company in await self._company_registry()
                if company.local_code == code
            ),
            None,
        )
        if company is None:
            raise SourceError(f"TWSE code {code} is not a currently listed issuer.")
        return company

    async def _report_rows(
        self, company_code: str, roc_year: int
    ) -> list[list[_TableCell]]:
        response = await self._request(
            "GET",
            DOCUMENT_SERVER_URL,
            params={
                "step": "1",
                "colorchg": "1",
                "co_id": company_code,
                "year": str(roc_year),
                "mtype": "F",
            },
        )
        if len(response.content) > _MAX_LIST_RESPONSE_BYTES:
            raise SourceError("The TWSE annual-report listing is unexpectedly large.")
        parser = _ReportTableParser()
        parser.feed(response.content.decode("big5", errors="replace"))
        return parser.rows

    @staticmethod
    def _company_from_row(row: dict[str, Any]) -> Company | None:
        code = str(row.get("公司代號") or "").strip().upper()
        name = str(row.get("公司名稱") or "").strip()
        if not name or not _COMPANY_CODE_PATTERN.fullmatch(code):
            return None
        short_name = str(row.get("公司簡稱") or "").strip()
        english_name = str(row.get("英文簡稱") or "").strip()
        profile_query = urlencode({"co_id": code})
        return Company(
            id=f"tw_twse_{code}",
            source_id=code,
            name=name,
            sources=("twse",),
            market="TW",
            country_code="TW",
            ticker=f"{code}.TW",
            local_code=code,
            english_name=english_name or short_name or None,
            status="listed issuer",
            company_type=str(row.get("產業別") or "").strip() or "TWSE issuer",
            address=str(row.get("住址") or "").strip() or None,
            source_url=f"{COMPANY_PROFILE_URL}?{profile_query}",
        )

    def _filing_from_row(
        self, row: list[_TableCell], *, company: Company
    ) -> Filing | None:
        link = next((cell.href for cell in row if cell.href), None)
        if not link:
            return None
        link_match = _DOWNLOAD_LINK_PATTERN.search(link)
        if link_match is None or link_match.group("code").upper() != company.local_code:
            return None
        details = self._annual_report_details(link_match.group("filename"))
        if details is None or details.code != company.local_code:
            return None
        posted_at = self._posted_at(row)
        return self._filing_from_details(
            details,
            company=company,
            published_at=posted_at,
        )

    @classmethod
    def _filing_from_details(
        cls,
        details: _AnnualReportDetails,
        *,
        company: Company,
        published_at: datetime | None = None,
    ) -> Filing:
        posted_at = published_at or cls._date_time(details.uploaded_on)
        language_name = "English " if details.language == "en" else ""
        document_url = cls._build_document_url(details.code, details.filename)
        return Filing(
            id=f"tw_mops_{details.filename.rsplit('.', 1)[0]}",
            company_id=company.id,
            source="twse",
            source_id=details.filename,
            title=(
                f"{language_name}annual report for year ended "
                f"{details.period_end.isoformat()}"
            ),
            category="accounts",
            filing_type=("annual_english" if details.language == "en" else "annual"),
            filing_date=posted_at.astimezone(_TAIPEI).date(),
            published_at=posted_at,
            period_end=details.period_end,
            document_id=document_url,
            media_type="application/pdf",
            issuer_name=company.name,
            language=details.language,
            pdf_available=True,
            source_url=document_url,
        )

    @staticmethod
    def _posted_at(row: list[_TableCell]) -> datetime | None:
        for cell in reversed(row):
            match = re.fullmatch(
                r"(?P<year>\d{2,3})/(?P<month>\d{2})/(?P<day>\d{2}) "
                r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})",
                cell.text,
            )
            if match is None:
                continue
            values = {name: int(value) for name, value in match.groupdict().items()}
            local = datetime(
                values["year"] + 1911,
                values["month"],
                values["day"],
                values["hour"],
                values["minute"],
                values["second"],
                tzinfo=_TAIPEI,
            )
            return local.astimezone(UTC)
        return None

    @staticmethod
    def _annual_report_details(filename: str) -> _AnnualReportDetails | None:
        match = _ANNUAL_REPORT_PATTERN.fullmatch(filename.strip())
        if match is None:
            return None
        try:
            period_end = date(int(match.group("period")), 12, 31)
            uploaded_on = date.fromisoformat(
                f"{match.group('upload')[:4]}-{match.group('upload')[4:6]}-"
                f"{match.group('upload')[6:]}"
            )
        except ValueError:
            return None
        return _AnnualReportDetails(
            filename=filename,
            code=match.group("code").upper(),
            period_end=period_end,
            uploaded_on=uploaded_on,
            language="en" if match.group("english") else "zh",
        )

    @staticmethod
    def _build_document_url(company_code: str, filename: str) -> str:
        query = urlencode(
            {
                "step": "9",
                "kind": "F",
                "co_id": company_code,
                "filename": filename,
            }
        )
        return f"{DOCUMENT_SERVER_URL}?{query}"

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                if attempt >= self._max_retries:
                    raise SourceError(f"TWSE request failed: {exc}") from exc
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            if response.status_code not in {429, 500, 502, 503, 504}:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise SourceError(
                        f"TWSE returned {response.status_code}: "
                        f"{response.text.strip()[:300] or response.reason_phrase}"
                    ) from exc
                return response
            if attempt >= self._max_retries:
                raise SourceError(
                    f"TWSE returned {response.status_code}: "
                    f"{response.text.strip()[:300] or response.reason_phrase}"
                )
            await asyncio.sleep(self._retry_delay(response, attempt))
        raise AssertionError("retry loop exited unexpectedly")

    @staticmethod
    def _pdf_from_archive(data: bytes) -> bytes:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = [
                    member
                    for member in archive.infolist()
                    if not member.is_dir()
                    and member.filename.casefold().endswith(".pdf")
                ]
                if (
                    len(members) != 1
                    or members[0].file_size > MAX_TAGGED_DOCUMENT_BYTES
                ):
                    raise DocumentUnavailableError(
                        "The TWSE report archive does not contain one safe PDF."
                    )
                return archive.read(members[0])
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise DocumentUnavailableError(
                "The TWSE report archive is invalid."
            ) from exc

    @staticmethod
    def _normalize_search(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.casefold())
        return " ".join(
            "".join(
                character
                for character in normalized
                if not unicodedata.combining(character)
            ).split()
        )

    @staticmethod
    def _date_time(value: date) -> datetime:
        return datetime.combine(value, datetime.min.time(), tzinfo=_TAIPEI).astimezone(
            UTC
        )

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        try:
            return min(float(retry_after), 30.0) if retry_after else 0.5 * (2**attempt)
        except ValueError:
            return 0.5 * (2**attempt)


@dataclass(frozen=True, slots=True)
class _AnnualReportDetails:
    filename: str
    code: str
    period_end: date
    uploaded_on: date
    language: str
