"""Official mainland China exchange and CNINFO public-filings client."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from urllib.parse import urlparse

import httpx

from openfilings.adapters._common import RetryingClient, ranked_matches
from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing, SourceName

SZSE_COMPANIES_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
SSE_COMPANIES_URL = "https://query.sse.com.cn/sseQuery/commonQuery.do"
ANNOUNCEMENTS_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
DOCUMENT_BASE = "https://static.cninfo.com.cn/"

_SSE_PARAMS = {
    "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
    "type": "inParams",
    "STOCK_TYPE": "1",
    "REG_PROVINCE": "",
    "CSRC_CODE": "",
    "STOCK_CODE": "",
    "COMPANY_STATUS": "2,4,5,7,8",
}
_MAX_REGISTRY_BYTES = 5 * 1024 * 1024


class CninfoClient(RetryingClient):
    """Search Shanghai/Shenzhen A-share issuers and retrieve annual reports."""

    source: SourceName = "cninfo"

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(
            "CNINFO",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.cninfo.com.cn/",
            },
            client=client,
        )
        self._today = today
        self._companies: tuple[Company, ...] | None = None

    async def search_companies(self, query: str, *, limit: int = 10) -> list[Company]:
        companies = await self._company_registry()
        clean_query = re.sub(r"^cn_cninfo_(?:sz|sh)_", "", query, flags=re.I)
        records = [
            (
                (
                    company.ticker or "",
                    company.name,
                    company.english_name or "",
                    company.local_code or "",
                ),
                company,
            )
            for company in companies
        ]
        return ranked_matches(clean_query, records, limit=limit)

    async def list_filings(
        self,
        company_id: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
    ) -> list[Filing]:
        exchange, code = self._company_key(company_id)
        company = next(
            (
                item
                for item in await self._company_registry()
                if item.id == f"cn_cninfo_{exchange}_{code}"
            ),
            None,
        )
        if company is None or not company.local_code:
            raise SourceError(
                f"CNINFO company {company_id} is not an active A-share issuer."
            )
        end = self._today()
        response = await self._request(
            "POST",
            ANNOUNCEMENTS_URL,
            data={
                "pageNum": "1",
                "pageSize": str(max(30, min(limit * 3, 100))),
                "column": "szse" if exchange == "sz" else "sse",
                "tabName": "fulltext",
                "plate": exchange,
                "stock": f"{code},{company.local_code}",
                "searchkey": "",
                "secid": "",
                "category": "category_ndbg_szsh",
                "trade": "",
                "seDate": f"{end.year - 10}-01-01~{end.isoformat()}",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true",
            },
            headers={
                "Referer": "https://www.cninfo.com.cn/",
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
        )
        try:
            rows = response.json().get("announcements") or []
        except (ValueError, AttributeError) as exc:
            raise SourceError("CNINFO returned an invalid report response.") from exc
        if not isinstance(rows, list):
            raise SourceError("CNINFO returned an invalid report response.")
        filings = [
            filing
            for row in rows
            if (filing := self._filing_from_row(company, row)) is not None
        ]
        filings.sort(key=lambda filing: filing.published_at, reverse=True)
        return filings[: max(1, limit)]

    async def download_document(self, document_id: str) -> SourceDocument:
        source_url = self.document_url(document_id)
        response = await self._request("GET", source_url)
        data = response.content
        if not data:
            raise DocumentUnavailableError("The CNINFO annual report was empty.")
        if len(data) > MAX_TAGGED_DOCUMENT_BYTES:
            raise DocumentUnavailableError(
                "The CNINFO report exceeds the 150 MB limit."
            )
        if not data.startswith(b"%PDF"):
            raise DocumentUnavailableError("CNINFO did not return a PDF report.")
        return SourceDocument(
            data=data,
            media_type="application/pdf",
            source_url=source_url,
        )

    def matches_company_id(self, value: str) -> bool:
        return value.casefold().startswith(("cn_cninfo_sz_", "cn_cninfo_sh_"))

    def matches_filing_id(self, value: str) -> bool:
        return value.casefold().startswith("cn_cninfo_filing_")

    @staticmethod
    def document_url(value: str) -> str:
        parsed = urlparse(value.strip())
        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != "static.cninfo.com.cn"
            or not parsed.path.startswith("/finalpage/")
            or ".." in parsed.path
            or not parsed.path.casefold().endswith(".pdf")
        ):
            raise DocumentUnavailableError("Unsafe CNINFO document URL.")
        return value.strip()

    async def _company_registry(self) -> tuple[Company, ...]:
        if self._companies is not None:
            return self._companies
        szse_response = await self._request("GET", SZSE_COMPANIES_URL)
        sse_response = await self._request(
            "GET",
            SSE_COMPANIES_URL,
            params=_SSE_PARAMS,
            headers={"Referer": "https://www.sse.com.cn/assortment/stock/list/share/"},
        )
        if (
            len(szse_response.content) > _MAX_REGISTRY_BYTES
            or len(sse_response.content) > _MAX_REGISTRY_BYTES
        ):
            raise SourceError("A China exchange registry is unexpectedly large.")
        try:
            szse_rows = szse_response.json().get("stockList", [])
            sse_rows = sse_response.json().get("result", [])
        except (ValueError, AttributeError) as exc:
            raise SourceError("A China exchange returned an invalid registry.") from exc
        companies = {
            company.id: company
            for exchange, rows in (("sz", szse_rows), ("sh", sse_rows))
            for row in rows
            if (company := self._company_from_row(exchange, row)) is not None
        }
        self._companies = tuple(companies.values())
        return self._companies

    @staticmethod
    def _company_from_row(exchange: str, row: object) -> Company | None:
        if not isinstance(row, dict):
            return None
        if exchange == "sz":
            code = str(row.get("code", "")).strip()
            name = str(row.get("zwjc", "")).strip()
            english_name = None
            org_id = str(row.get("orgId", "")).strip()
            category = str(row.get("category", "")).strip()
            if category != "A股":
                return None
            if org_id.casefold().startswith("gssh"):
                exchange = "sh"
        else:
            code = str(row.get("A_STOCK_CODE", "")).strip()
            name = str(row.get("FULL_NAME", "")).strip()
            english_name = str(row.get("FULL_NAME_IN_ENGLISH", "")).strip() or None
            org_id = f"gssh0{code}"
        if not re.fullmatch(r"\d{6}", code) or not name:
            return None
        return Company(
            id=f"cn_cninfo_{exchange}_{code}",
            source_id=code,
            name=name,
            sources=("cninfo",),
            market="CN",
            country_code="CN",
            ticker=code,
            local_code=org_id,
            english_name=english_name,
            status="active A-share issuer",
            company_type=f"{'Shenzhen' if exchange == 'sz' else 'Shanghai'} A-share",
            source_url=(
                "https://www.cninfo.com.cn/new/disclosure/stock?"
                f"stockCode={code}&orgId={org_id}"
            ),
        )

    def _filing_from_row(self, company: Company, row: object) -> Filing | None:
        if not isinstance(row, dict):
            return None
        title = re.sub(r"<[^>]+>", "", str(row.get("announcementTitle", ""))).strip()
        if not title or "摘要" in title or "取消" in title:
            return None
        announcement_id = str(row.get("announcementId", "")).strip()
        relative_url = str(row.get("adjunctUrl", "")).strip().lstrip("/")
        if not announcement_id.isdigit() or not relative_url:
            return None
        source_url = f"{DOCUMENT_BASE}{relative_url}"
        try:
            source_url = self.document_url(source_url)
            published_at = datetime.fromtimestamp(
                int(row.get("announcementTime", 0)) / 1000,
                tz=UTC,
            )
        except (DocumentUnavailableError, TypeError, ValueError, OSError):
            return None
        year_match = re.search(r"(20\d{2})年", title)
        period_end = date(int(year_match.group(1)), 12, 31) if year_match else None
        return Filing(
            id=f"cn_cninfo_filing_{announcement_id}",
            company_id=company.id,
            source="cninfo",
            source_id=announcement_id,
            title=title,
            category="accounts",
            filing_type="annual",
            filing_date=published_at.date(),
            published_at=published_at,
            period_end=period_end,
            document_id=source_url,
            media_type="application/pdf",
            issuer_name=company.name,
            language="zh",
            pdf_available=True,
            source_url=source_url,
        )

    @staticmethod
    def _company_key(value: str) -> tuple[str, str]:
        match = re.fullmatch(r"cn_cninfo_(sz|sh)_(\d{6})", value.strip(), re.I)
        if match is None:
            raise SourceError(
                "Expected a China company ID shaped like cn_cninfo_sz_000001."
            )
        return match.group(1).casefold(), match.group(2)
