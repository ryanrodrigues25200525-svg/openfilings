"""Official HKEX and HKEXnews client for Hong Kong-listed issuer reports."""

from __future__ import annotations

import asyncio
import html
import io
import json
import re
import unicodedata
import zipfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing

SECURITIES_URL = (
    "https://www.hkex.com.hk/eng/services/trading/securities/"
    "securitieslists/ListOfSecurities.xlsx"
)
PREFIX_URL = "https://www1.hkexnews.hk/search/prefix.do"
TITLE_SEARCH_URL = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
TITLE_SEARCH_PAGE_URL = "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en"
HKEXNEWS_ORIGIN = "https://www1.hkexnews.hk"

_HONG_KONG = ZoneInfo("Asia/Hong_Kong")
_COMPANY_CODE_PATTERN = re.compile(r"^\d{5}$")
_FILING_PATH_PATTERN = re.compile(
    r"/listedco/listconews/sehk/\d{4}/\d{4}/\d{13}\.pdf", re.IGNORECASE
)
_JSONP_PATTERN = re.compile(r"^callback\((?P<payload>.*)\);?\s*$", re.DOTALL)
_CELL_COLUMN_PATTERN = re.compile(r"^[A-Z]+")
_SPREADSHEET_NAMESPACE = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_ISSUER_SUBCATEGORIES = {
    "Equity Securities (Main Board)",
    "Equity Securities (GEM)",
}
_REPORT_CATEGORIES = {
    "40100": ("Annual Report", "annual"),
    "40200": ("Interim/Half-Year Report", "interim"),
}
_MAX_SPREADSHEET_BYTES = 5 * 1024 * 1024
_MAX_WORKSHEET_BYTES = 64 * 1024 * 1024
_MAX_SEARCH_RESPONSE_BYTES = 5 * 1024 * 1024
_MAX_PREFIX_RESPONSE_BYTES = 1024 * 1024


class HkexClient:
    """Search current HKEX issuers and retrieve HKEXnews financial reports."""

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
                "Accept-Language": "en-HK,en;q=0.9",
            },
        )
        self._companies: tuple[Company, ...] | None = None

    async def __aenter__(self) -> HkexClient:
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
        normalized = self._normalize_search(self._without_company_prefix(clean_query))
        ranked: list[tuple[int, str, Company]] = []
        for company in companies:
            fields = (
                company.local_code or "",
                company.ticker or "",
                company.name,
            )
            normalized_fields = tuple(self._normalize_search(value) for value in fields)
            if not any(normalized in value for value in normalized_fields):
                continue
            if normalized in normalized_fields[:2]:
                rank = 0
            elif normalized_fields[2].startswith(normalized):
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
        stock_id = await self._stock_id(code)
        result_sets = await asyncio.gather(
            *(
                self._report_rows(stock_id, report_code)
                for report_code in _REPORT_CATEGORIES
            )
        )
        filings: dict[str, Filing] = {}
        for rows in result_sets:
            for row in rows:
                filing = self._filing_from_row(row, company=company)
                if filing is not None:
                    filings[filing.id] = filing
        return sorted(
            filings.values(),
            key=lambda item: (
                item.published_at or self._date_time(item.filing_date),
                item.id,
            ),
            reverse=True,
        )[: max(1, limit)]

    async def download_document(self, document_url: str) -> SourceDocument:
        source_url = self.document_url(document_url)
        response = await self._request("GET", source_url)
        data = response.content
        if not data:
            raise DocumentUnavailableError("The HKEX filing document was empty.")
        if len(data) > MAX_TAGGED_DOCUMENT_BYTES:
            raise DocumentUnavailableError("The HKEX filing exceeds the 150 MB limit.")
        if not data.startswith(b"%PDF"):
            raise DocumentUnavailableError(
                "HKEX returned an invalid response instead of the filing PDF."
            )
        return SourceDocument(
            data=data,
            media_type="application/pdf",
            source_url=source_url,
        )

    def matches_company_id(self, value: str) -> bool:
        try:
            self.normalize_company_code(value)
        except SourceError:
            return False
        return value.casefold().startswith("hk_hkex_")

    @staticmethod
    def normalize_company_code(value: str) -> str:
        clean_value = HkexClient._without_company_prefix(value.strip())
        if not _COMPANY_CODE_PATTERN.fullmatch(clean_value):
            raise SourceError(
                "Expected an HKEX company ID shaped like hk_hkex_{five-digit_code}."
            )
        return clean_value

    @staticmethod
    def document_url(value: str) -> str:
        url = value.strip()
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != "www1.hkexnews.hk"
            or _FILING_PATH_PATTERN.fullmatch(parsed.path) is None
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise DocumentUnavailableError("Unsafe HKEX document URL.")
        return url

    async def _company_registry(self) -> tuple[Company, ...]:
        if self._companies is not None:
            return self._companies
        response = await self._request("GET", SECURITIES_URL)
        if len(response.content) > _MAX_SPREADSHEET_BYTES:
            raise SourceError("The HKEX securities spreadsheet is unexpectedly large.")
        self._companies = self._companies_from_workbook(response.content)
        return self._companies

    async def _company_by_code(self, code: str) -> Company:
        company = next(
            (
                candidate
                for candidate in await self._company_registry()
                if candidate.local_code == code
            ),
            None,
        )
        if company is None:
            raise SourceError(
                f"HKEX code {code} is not a current listed issuer equity."
            )
        return company

    async def _stock_id(self, code: str) -> str:
        response = await self._request(
            "GET",
            PREFIX_URL,
            params={
                "callback": "callback",
                "lang": "EN",
                "type": "A",
                "name": code,
                "market": "SEHK",
            },
        )
        if len(response.content) > _MAX_PREFIX_RESPONSE_BYTES:
            raise SourceError("The HKEX company lookup response is unexpectedly large.")
        match = _JSONP_PATTERN.fullmatch(response.text.strip())
        if match is None:
            raise SourceError("HKEX returned an invalid company lookup response.")
        try:
            payload = json.loads(match.group("payload"))
        except json.JSONDecodeError as exc:
            raise SourceError("HKEX returned invalid company lookup JSON.") from exc
        records = payload.get("stockInfo", []) if isinstance(payload, dict) else []
        for record in records:
            if not isinstance(record, dict) or str(record.get("code")) != code:
                continue
            stock_id = str(record.get("stockId", ""))
            if stock_id.isdigit():
                return stock_id
        raise SourceError(f"HKEX could not resolve the filing-search ID for {code}.")

    async def _report_rows(
        self, stock_id: str, report_code: str
    ) -> list[dict[str, Any]]:
        today = self._today()
        response = await self._request(
            "GET",
            TITLE_SEARCH_URL,
            params={
                "sortDir": "0",
                "sortByOptions": "DateTime",
                "category": "0",
                "market": "SEHK",
                "stockId": stock_id,
                "documentType": "-1",
                "fromDate": f"{today.year - self._history_years}0101",
                "toDate": today.strftime("%Y%m%d"),
                "title": "",
                "searchType": "1",
                "t1code": "40000",
                "t2Gcode": "-1",
                "t2code": report_code,
                "rowRange": "100",
                "lang": "en",
            },
        )
        if len(response.content) > _MAX_SEARCH_RESPONSE_BYTES:
            raise SourceError("The HKEX filing-search response is unexpectedly large.")
        try:
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(
                payload.get("result"), str
            ):
                raise TypeError
            rows = json.loads(payload["result"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SourceError(
                "HKEX returned an invalid filing-search response."
            ) from exc
        if payload.get("hasNextRow"):
            raise SourceError(
                "HKEX returned more than 100 matching reports; narrow the "
                "history range."
            )
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SourceError("HKEX returned invalid filing-search records.")
        return rows

    @classmethod
    def _companies_from_workbook(cls, data: bytes) -> tuple[Company, ...]:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                member = archive.getinfo("xl/worksheets/sheet1.xml")
                if member.file_size > _MAX_WORKSHEET_BYTES:
                    raise SourceError("The HKEX securities worksheet is too large.")
                shared_strings = cls._shared_strings(archive)
                with archive.open(member) as worksheet:
                    rows = cls._worksheet_rows(worksheet, shared_strings)
        except (
            ElementTree.ParseError,
            KeyError,
            zipfile.BadZipFile,
            RuntimeError,
        ) as exc:
            raise SourceError("The HKEX securities spreadsheet is invalid.") from exc

        companies: dict[str, Company] = {}
        seen_securities: set[str] = set()
        for row in rows:
            code = row.get("A", "").strip()
            name = row.get("B", "").strip()
            category = row.get("C", "").strip()
            subcategory = row.get("D", "").strip()
            isin = row.get("F", "").strip()
            if (
                not name
                or not _COMPANY_CODE_PATTERN.fullmatch(code)
                or category != "Equity"
                or subcategory not in _ISSUER_SUBCATEGORIES
            ):
                continue
            security_key = isin or code
            if security_key in seen_securities:
                continue
            seen_securities.add(security_key)
            ticker_code = str(int(code)).zfill(4)
            company = Company(
                id=f"hk_hkex_{code}",
                source_id=code,
                name=name,
                sources=("hkex",),
                market="HK",
                country_code="HK",
                ticker=f"{ticker_code}.HK",
                local_code=code,
                status="listed issuer",
                company_type=subcategory,
                source_url=TITLE_SEARCH_PAGE_URL,
            )
            companies[company.id] = company
        if not companies:
            raise SourceError("The HKEX securities spreadsheet contained no issuers.")
        return tuple(companies.values())

    @staticmethod
    def _shared_strings(archive: zipfile.ZipFile) -> tuple[str, ...]:
        try:
            member = archive.getinfo("xl/sharedStrings.xml")
        except KeyError:
            return ()
        if member.file_size > _MAX_SPREADSHEET_BYTES:
            raise SourceError("The HKEX shared-string table is unexpectedly large.")
        root = ElementTree.parse(archive.open(member)).getroot()
        return tuple(
            "".join(node.text or "" for node in item.iter(f"{_SPREADSHEET_NAMESPACE}t"))
            for item in root.findall(f"{_SPREADSHEET_NAMESPACE}si")
        )

    @staticmethod
    def _worksheet_rows(
        worksheet: Any, shared_strings: tuple[str, ...]
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for _, element in ElementTree.iterparse(worksheet, events=("end",)):
            if element.tag == f"{_SPREADSHEET_NAMESPACE}c":
                reference = element.get("r", "")
                column_match = _CELL_COLUMN_PATTERN.match(reference)
                if column_match and column_match.group() in {"A", "B", "C", "D", "F"}:
                    value_node = element.find(f"{_SPREADSHEET_NAMESPACE}v")
                    value = value_node.text if value_node is not None else ""
                    if element.get("t") == "s" and value.isdigit():
                        index = int(value)
                        value = (
                            shared_strings[index] if index < len(shared_strings) else ""
                        )
                    current[column_match.group()] = value or ""
            elif element.tag == f"{_SPREADSHEET_NAMESPACE}row":
                if current:
                    rows.append(current)
                current = {}
                element.clear()
        return rows

    @classmethod
    def _filing_from_row(
        cls, row: dict[str, Any], *, company: Company
    ) -> Filing | None:
        news_id = str(row.get("NEWS_ID") or "").strip()
        file_type = str(row.get("FILE_TYPE") or "").strip().upper()
        report_type = cls._report_type(str(row.get("LONG_TEXT") or ""))
        if not news_id.isdigit() or file_type != "PDF" or report_type is None:
            return None
        document_url = cls._safe_document_link(str(row.get("FILE_LINK") or ""))
        if document_url is None:
            return None
        published_at = cls._published_at(str(row.get("DATE_TIME") or ""))
        if published_at is None:
            return None
        title = cls._clean_text(str(row.get("TITLE") or ""))
        if not title:
            return None
        return Filing(
            id=f"hk_hkex_{news_id}",
            company_id=company.id,
            source="hkex",
            source_id=news_id,
            title=title,
            category="accounts",
            filing_type=report_type,
            filing_date=published_at.astimezone(_HONG_KONG).date(),
            published_at=published_at,
            description=cls._clean_text(str(row.get("LONG_TEXT") or "")) or None,
            document_id=document_url,
            media_type="application/pdf",
            issuer_name=company.name,
            language="en",
            pdf_available=True,
            source_url=document_url,
        )

    @staticmethod
    def _report_type(value: str) -> str | None:
        cleaned = HkexClient._clean_text(value)
        for label, filing_type in _REPORT_CATEGORIES.values():
            if f"[{label}]" in cleaned:
                return filing_type
        return None

    @staticmethod
    def _safe_document_link(value: str) -> str | None:
        url = urljoin(HKEXNEWS_ORIGIN, value.strip())
        try:
            return HkexClient.document_url(url)
        except DocumentUnavailableError:
            return None

    @staticmethod
    def _published_at(value: str) -> datetime | None:
        try:
            local = datetime.strptime(value.strip(), "%d/%m/%Y %H:%M").replace(
                tzinfo=_HONG_KONG
            )
        except ValueError:
            return None
        return local.astimezone(UTC)

    @staticmethod
    def _clean_text(value: str) -> str:
        unescaped = html.unescape(value)
        without_tags = re.sub(r"<[^>]*>", " ", unescaped)
        return " ".join(without_tags.split())

    @staticmethod
    def _without_company_prefix(value: str) -> str:
        prefix = "hk_hkex_"
        return value[len(prefix) :] if value.casefold().startswith(prefix) else value

    @staticmethod
    def _normalize_search(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(character for character in normalized if character.isalnum())

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                if attempt >= self._max_retries:
                    raise SourceError(f"HKEX request failed: {exc}") from exc
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            if response.status_code not in {429, 500, 502, 503, 504}:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise SourceError(
                        f"HKEX returned {response.status_code}: "
                        f"{response.text.strip()[:300] or response.reason_phrase}"
                    ) from exc
                return response
            if attempt >= self._max_retries:
                raise SourceError(
                    f"HKEX returned {response.status_code}: "
                    f"{response.text.strip()[:300] or response.reason_phrase}"
                )
            await asyncio.sleep(self._retry_delay(response, attempt))
        raise AssertionError("retry loop exited unexpectedly")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        try:
            return min(float(retry_after), 30.0) if retry_after else 0.5 * (2**attempt)
        except ValueError:
            return 0.5 * (2**attempt)

    @staticmethod
    def _date_time(value: date) -> datetime:
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC)
