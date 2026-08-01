"""Official SGX client for Singapore-listed companies and annual reports."""

from __future__ import annotations

import asyncio
import codecs
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx

from openfilings._version import __version__
from openfilings.adapters._common import bounded_request
from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing

STOCKS_URL = "https://api.sgx.com/securities/v1.1/stocks"
METADATA_URL = "https://api.sgx.com/marketmetadata/v2"
FINANCIAL_REPORTS_URL = "https://api.sgx.com/financialreports/v1.0"
ANNOUNCEMENTS_URL = "https://api.sgx.com/announcements/v1.1/securitycode"
APP_CONFIG_URL = "https://www.sgx.com/config/appconfig.json"
CMS_API_URL = "https://api2.sgx.com/content-api/"
CORPORATE_INFORMATION_URL = "https://www.sgx.com/securities/corporate-information"
SGX_LINKS_ORIGIN = "https://links.sgx.com"

_SINGAPORE = ZoneInfo("Asia/Singapore")
_LISTED_MARKETS = {"MAINBOARD", "CATALIST"}
_IBM_CODE_PATTERN = re.compile(r"^[A-Z0-9]{3,4}$")
_STOCK_CODE_PATTERN = re.compile(r"^[A-Z0-9]{3,4}$")
_ANNOUNCEMENT_ID_PATTERN = r"[A-Z0-9]{16}"
_DETAIL_PATH_PATTERN = re.compile(
    rf"^/1\.0\.0/corporate-announcements/"
    rf"(?P<announcement>{_ANNOUNCEMENT_ID_PATTERN})/[a-f0-9]{{64}}$"
)
_ATTACHMENT_PATH_PATTERN = re.compile(
    rf"^/1\.0\.0/corporate-announcements/"
    rf"(?P<announcement>{_ANNOUNCEMENT_ID_PATTERN})/"
    r"(?P<filename>\d+_[^/?#]+\.pdf)$",
    re.IGNORECASE,
)
_REJECTED_ATTACHMENT_TERMS = {
    "appendix",
    "circular",
    "letter",
    "notice",
    "proxy",
    "request",
    "sustainability",
}
_MAX_STOCKS_BYTES = 2 * 1024 * 1024
_MAX_METADATA_BYTES = 16 * 1024 * 1024
_MAX_REPORTS_BYTES = 5 * 1024 * 1024
_MAX_ANNOUNCEMENTS_BYTES = 5 * 1024 * 1024
_MAX_CONFIG_BYTES = 64 * 1024
_MAX_DETAIL_BYTES = 2 * 1024 * 1024
_MAX_REPORT_PAGES = 20


class SgxClient:
    """Search SGX issuers and retrieve public annual-report PDFs."""

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
                "User-Agent": f"openfilings/{__version__}",
                "Accept-Language": "en-SG,en;q=0.9",
            },
        )
        self._companies: tuple[Company, ...] | None = None
        self._search_aliases: dict[str, tuple[str, ...]] = {}
        self._announcement_token: str | None = None

    async def __aenter__(self) -> SgxClient:
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
            identity_fields = (
                company.source_id,
                company.local_code or "",
                company.ticker or "",
            )
            aliases = self._search_aliases.get(company.id, ())
            name_fields = (company.name, company.english_name or "", *aliases)
            normalized_identity = tuple(
                self._normalize_search(value) for value in identity_fields
            )
            normalized_names = tuple(
                self._normalize_search(value) for value in name_fields
            )
            if not any(
                normalized in value
                for value in (*normalized_identity, *normalized_names)
            ):
                continue
            if normalized in normalized_identity:
                rank = 0
            elif any(value.startswith(normalized) for value in normalized_names):
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
        normalized_category = category.casefold() if category else "accounts"
        if normalized_category not in {"accounts", "dividend"}:
            return []
        ibm_code = self.normalize_ibm_code(company_id_or_code)
        company = await self._company_by_ibm_code(ibm_code)
        cutoff = date(self._today().year - self._history_years, 1, 1)
        filings: dict[str, Filing] = {}
        if normalized_category == "dividend":
            rows = await self._announcement_rows(company.local_code or "")
            filing_parser = self._dividend_filing_from_row
        else:
            rows = await self._report_rows(company.name)
            filing_parser = self._filing_from_row
        for row in rows:
            filing = filing_parser(row, company=company)
            if (
                filing is not None
                and (filing.period_end or filing.filing_date) >= cutoff
            ):
                filings[filing.id] = filing
        return sorted(
            filings.values(),
            key=lambda item: (
                item.published_at or self._date_time(item.filing_date),
                item.id,
            ),
            reverse=True,
        )[: max(1, limit)]

    async def download_document(self, detail_url: str) -> SourceDocument:
        source_page = self.detail_url(detail_url)
        detail_response = await self._request("GET", source_page)
        if len(detail_response.content) > _MAX_DETAIL_BYTES:
            raise DocumentUnavailableError("The SGX report page is unexpectedly large.")
        parser = _AttachmentParser()
        try:
            parser.feed(detail_response.text)
        except Exception as exc:
            raise DocumentUnavailableError("The SGX report page is invalid.") from exc

        announcement_id = self._announcement_id(source_page)
        candidates = []
        for href in parser.links:
            candidate = self._safe_attachment_url(href, announcement_id)
            if candidate is not None and self._attachment_score(candidate)[0] >= 0:
                candidates.append(candidate)
        if not candidates:
            raise DocumentUnavailableError(
                "The SGX report page did not expose a safe PDF attachment."
            )
        document_url = max(candidates, key=self._attachment_score)
        response = await self._request("GET", document_url)
        data = response.content
        if not data:
            raise DocumentUnavailableError("The SGX annual report was empty.")
        if len(data) > MAX_TAGGED_DOCUMENT_BYTES:
            raise DocumentUnavailableError("The SGX annual report exceeds 150 MB.")
        if not data.startswith(b"%PDF"):
            raise DocumentUnavailableError(
                "SGX returned an invalid response instead of the annual-report PDF."
            )
        return SourceDocument(
            data=data,
            media_type="application/pdf",
            source_url=document_url,
        )

    def matches_company_id(self, value: str) -> bool:
        if not value.casefold().startswith("sg_sgx_"):
            return False
        try:
            self.normalize_ibm_code(value)
        except SourceError:
            return False
        return True

    @staticmethod
    def normalize_ibm_code(value: str) -> str:
        clean_value = SgxClient._without_company_prefix(value.strip()).upper()
        if not _IBM_CODE_PATTERN.fullmatch(clean_value):
            raise SourceError(
                "Expected an SGX company ID shaped like sg_sgx_{IBM_code}."
            )
        return clean_value

    @staticmethod
    def detail_url(value: str) -> str:
        url = value.strip()
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != "links.sgx.com"
            or _DETAIL_PATH_PATTERN.fullmatch(parsed.path) is None
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise DocumentUnavailableError("Unsafe SGX report-detail URL.")
        return url

    async def _company_registry(self) -> tuple[Company, ...]:
        if self._companies is not None:
            return self._companies
        stocks_response, metadata_response = await asyncio.gather(
            self._request(
                "GET",
                STOCKS_URL,
                params={
                    "params": (
                        "nc,n,type,ls,m,sc,bl,sip,ex,ej,clo,cr,cur,el,r,i,cc,ig,lf"
                    )
                },
            ),
            self._request("GET", METADATA_URL),
        )
        if len(stocks_response.content) > _MAX_STOCKS_BYTES:
            raise SourceError("The SGX stocks response is unexpectedly large.")
        if len(metadata_response.content) > _MAX_METADATA_BYTES:
            raise SourceError("The SGX metadata response is unexpectedly large.")
        stock_rows = self._json_records(stocks_response, path=("data", "prices"))
        metadata_rows = self._json_records(metadata_response, path=("data",))
        metadata_by_stock = {
            str(row.get("stockCode", "")).upper(): row
            for row in metadata_rows
            if _STOCK_CODE_PATTERN.fullmatch(str(row.get("stockCode", "")).upper())
        }

        companies: dict[str, Company] = {}
        aliases: dict[str, list[str]] = {}
        for stock in stock_rows:
            market = str(stock.get("m", "")).upper()
            stock_code = str(stock.get("nc", "")).upper()
            if (
                market not in _LISTED_MARKETS
                or str(stock.get("type", "")).casefold() != "stocks"
                or not _STOCK_CODE_PATTERN.fullmatch(stock_code)
            ):
                continue
            metadata = metadata_by_stock.get(stock_code)
            if metadata is None:
                continue
            ibm_code = str(metadata.get("ibmCode", "")).upper()
            issuer_name = " ".join(str(metadata.get("issuerName", "")).split())
            if not _IBM_CODE_PATTERN.fullmatch(ibm_code) or not issuer_name:
                continue
            company_id = f"sg_sgx_{ibm_code}"
            short_name = " ".join(str(stock.get("n", "")).split())
            aliases.setdefault(company_id, []).extend((stock_code, short_name))
            companies.setdefault(
                company_id,
                Company(
                    id=company_id,
                    source_id=ibm_code,
                    name=issuer_name,
                    sources=("sgx",),
                    market="SG",
                    country_code="SG",
                    ticker=f"{stock_code}.SI",
                    local_code=stock_code,
                    english_name=short_name or None,
                    status=f"{market.title()} listed issuer",
                    company_type=market,
                    source_url=CORPORATE_INFORMATION_URL,
                ),
            )
        if not companies:
            raise SourceError("The SGX feeds contained no listed companies.")
        self._search_aliases = {
            company_id: tuple(dict.fromkeys(values))
            for company_id, values in aliases.items()
        }
        self._companies = tuple(companies.values())
        return self._companies

    async def _company_by_ibm_code(self, ibm_code: str) -> Company:
        company = next(
            (
                candidate
                for candidate in await self._company_registry()
                if candidate.source_id == ibm_code
            ),
            None,
        )
        if company is None:
            raise SourceError(
                f"SGX code {ibm_code} is not a current Mainboard or Catalist issuer."
            )
        return company

    async def _report_rows(self, company_name: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 0
        while page < _MAX_REPORT_PAGES:
            response = await self._request(
                "GET",
                FINANCIAL_REPORTS_URL,
                params={
                    "pagestart": page,
                    "pagesize": 100,
                    "companyname": company_name,
                },
            )
            if len(response.content) > _MAX_REPORTS_BYTES:
                raise SourceError("The SGX financial-report response is too large.")
            payload = self._json_object(response, "financial-report")
            rows = payload.get("data")
            meta = payload.get("meta")
            if not isinstance(rows, list) or not all(
                isinstance(row, dict) for row in rows
            ):
                raise SourceError("SGX returned invalid financial-report records.")
            if not isinstance(meta, dict):
                raise SourceError("SGX returned invalid financial-report paging data.")
            total_pages = meta.get("totalPages")
            if not isinstance(total_pages, int) or not (
                0 <= total_pages <= _MAX_REPORT_PAGES
            ):
                raise SourceError("SGX returned invalid financial-report page counts.")
            records.extend(rows)
            page += 1
            if page >= total_pages:
                return records
        raise SourceError("SGX returned too many financial-report pages.")

    async def _announcement_rows(self, stock_code: str) -> list[dict[str, Any]]:
        token = await self._announcement_authorization_token()
        response = await self._request(
            "GET",
            ANNOUNCEMENTS_URL,
            params={
                "value": stock_code,
                "periodstart": (
                    f"{self._today().year - self._history_years}0101_000000"
                ),
                "periodend": self._today().strftime("%Y%m%d_235959"),
                "pagestart": 0,
                "pagesize": 1000,
            },
            headers={
                "Referer": "https://www.sgx.com/securities/company-announcements",
                "authorizationToken": token,
            },
        )
        if len(response.content) > _MAX_ANNOUNCEMENTS_BYTES:
            raise SourceError("The SGX announcement response is unexpectedly large.")
        payload = self._json_object(response, "announcement")
        rows = payload.get("data")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise SourceError("SGX returned invalid announcement records.")
        return rows

    async def _announcement_authorization_token(self) -> str:
        if self._announcement_token is not None:
            return self._announcement_token
        config_response = await self._request("GET", APP_CONFIG_URL)
        if len(config_response.content) > _MAX_CONFIG_BYTES:
            raise SourceError("The SGX application config is unexpectedly large.")
        config = self._json_object(config_response, "application-config")
        cms_version = str(config.get("CMS_VERSION") or "").strip()
        if not re.fullmatch(r"[a-f0-9]{40}", cms_version):
            raise SourceError("SGX returned an invalid CMS version.")
        validator_response = await self._request(
            "GET",
            CMS_API_URL,
            params={"queryId": f"{cms_version}:we_chat_qr_validator"},
            headers={"Referer": "https://www.sgx.com/securities/company-announcements"},
        )
        if len(validator_response.content) > _MAX_CONFIG_BYTES:
            raise SourceError("The SGX announcement validator is unexpectedly large.")
        validator_payload = self._json_object(
            validator_response, "announcement-validator"
        )
        data = validator_payload.get("data")
        validator = data.get("qrValidator") if isinstance(data, dict) else None
        if not isinstance(validator, str) or not validator.strip():
            raise SourceError("SGX returned an invalid announcement validator.")
        self._announcement_token = codecs.decode(validator.strip(), "rot_13")
        return self._announcement_token

    @classmethod
    def _filing_from_row(
        cls, row: dict[str, Any], *, company: Company
    ) -> Filing | None:
        if str(row.get("title", "")).strip().casefold() != "annual report":
            return None
        issuer_names = {
            cls._normalize_search(str(row.get(key, "")))
            for key in ("companyName", "securityName")
        }
        if cls._normalize_search(company.name) not in issuer_names:
            return None
        announcement_id = str(row.get("id", "")).strip().upper()
        if re.fullmatch(_ANNOUNCEMENT_ID_PATTERN, announcement_id) is None:
            return None
        try:
            source_url = cls.detail_url(str(row.get("url", "")))
        except DocumentUnavailableError:
            return None
        if cls._announcement_id(source_url) != announcement_id:
            return None
        period_end = cls._epoch_milliseconds_date(row.get("documentDate"))
        published_at = cls._epoch_milliseconds(row.get("broadcastDateTime"))
        if period_end is None or published_at is None:
            return None
        return Filing(
            id=f"sg_sgx_{announcement_id}",
            company_id=company.id,
            source="sgx",
            source_id=announcement_id,
            title=f"{period_end.year} Annual Report",
            category="accounts",
            filing_type="annual",
            filing_date=published_at.astimezone(_SINGAPORE).date(),
            published_at=published_at,
            period_end=period_end,
            description="Annual Report",
            document_id=source_url,
            media_type="application/pdf",
            issuer_name=company.name,
            language="en",
            pdf_available=True,
            source_url=source_url,
        )

    @classmethod
    def _dividend_filing_from_row(
        cls, row: dict[str, Any], *, company: Company
    ) -> Filing | None:
        category_name = str(row.get("category_name") or "").strip()
        if "dividend" not in cls._normalize_search(category_name):
            return None
        announcement_id = str(row.get("id") or "").strip().upper()
        if re.fullmatch(_ANNOUNCEMENT_ID_PATTERN, announcement_id) is None:
            return None
        try:
            source_url = cls.detail_url(str(row.get("url") or ""))
        except DocumentUnavailableError:
            return None
        if cls._announcement_id(source_url) != announcement_id:
            return None
        published_at = cls._epoch_milliseconds(
            row.get("broadcast_date_time") or row.get("submission_date_time")
        )
        filing_date = (
            published_at.astimezone(_SINGAPORE).date()
            if published_at
            else cls._compact_date(row.get("submission_date"))
        )
        if filing_date is None:
            return None
        title = str(row.get("title") or category_name).strip()
        if not title:
            return None
        return Filing(
            id=f"sg_sgx_{announcement_id}",
            company_id=company.id,
            source="sgx",
            source_id=announcement_id,
            title=title,
            category="dividend",
            filing_type="dividend",
            filing_date=filing_date,
            published_at=published_at,
            description=category_name,
            document_id=source_url,
            media_type="application/pdf",
            issuer_name=company.name,
            language="en",
            source_url=source_url,
        )

    @staticmethod
    def _epoch_milliseconds(value: Any) -> datetime | None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None

    @classmethod
    def _epoch_milliseconds_date(cls, value: Any) -> date | None:
        timestamp = cls._epoch_milliseconds(value)
        return timestamp.astimezone(_SINGAPORE).date() if timestamp else None

    @staticmethod
    def _compact_date(value: Any) -> date | None:
        try:
            return datetime.strptime(str(value), "%Y%m%d").date()
        except ValueError:
            return None

    @staticmethod
    def _announcement_id(detail_url: str) -> str:
        match = _DETAIL_PATH_PATTERN.fullmatch(urlparse(detail_url).path)
        if match is None:
            raise DocumentUnavailableError("Unsafe SGX report-detail URL.")
        return match.group("announcement").upper()

    @classmethod
    def _safe_attachment_url(cls, href: str, announcement_id: str) -> str | None:
        url = urljoin(SGX_LINKS_ORIGIN, href.strip())
        parsed = urlparse(url)
        match = _ATTACHMENT_PATH_PATTERN.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != "links.sgx.com"
            or match is None
            or match.group("announcement").upper() != announcement_id
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            return None
        filename = unquote(match.group("filename"))
        if "/" in filename or "\\" in filename or ".." in filename:
            return None
        return url

    @staticmethod
    def _attachment_score(url: str) -> tuple[int, str]:
        filename = unquote(urlparse(url).path.rsplit("/", 1)[-1])
        normalized = SgxClient._normalize_search(filename)
        score = 10 if "annualreport" in normalized else 0
        if any(term in normalized for term in _REJECTED_ATTACHMENT_TERMS):
            score -= 100
        return score, filename

    @staticmethod
    def _json_records(
        response: httpx.Response, *, path: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        payload = SgxClient._json_object(response, "market")
        value: Any = payload
        for key in path:
            if not isinstance(value, dict):
                raise SourceError("SGX returned an invalid market response.")
            value = value.get(key)
        if not isinstance(value, list) or not all(
            isinstance(row, dict) for row in value
        ):
            raise SourceError("SGX returned invalid market records.")
        return value

    @staticmethod
    def _json_object(response: httpx.Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(f"SGX returned invalid {label} JSON.") from exc
        if not isinstance(payload, dict):
            raise SourceError(f"SGX returned an invalid {label} response.")
        return payload

    @staticmethod
    def _without_company_prefix(value: str) -> str:
        prefix = "sg_sgx_"
        return value[len(prefix) :] if value.casefold().startswith(prefix) else value

    @staticmethod
    def _normalize_search(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return "".join(character for character in normalized if character.isalnum())

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = await bounded_request(self._client, method, url, **kwargs)
            except httpx.RequestError as exc:
                if attempt >= self._max_retries:
                    raise SourceError(f"SGX request failed: {exc}") from exc
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            if response.status_code not in {429, 500, 502, 503, 504}:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise SourceError(
                        f"SGX returned {response.status_code}: "
                        f"{response.text.strip()[:300] or response.reason_phrase}"
                    ) from exc
                return response
            if attempt >= self._max_retries:
                raise SourceError(
                    f"SGX returned {response.status_code}: "
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


class _AttachmentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = next((value for key, value in attrs if key.casefold() == "href"), None)
        if href:
            self.links.append(href)
