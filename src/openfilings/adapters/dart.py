"""Official OPENDART client for South Korean corporate filings (FSS)."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime
from typing import Any

import httpx

from openfilings.adapters._common import RetryingClient, ranked_matches, utc_midnight
from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import (
    ConfigurationError,
    DocumentUnavailableError,
    SourceError,
)
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing

API_BASE_URL = "https://opendart.fss.or.kr/api"

_CORP_CODE = re.compile(r"^\d{8}$")
_RCEPT_NO = re.compile(r"^\d{14}$")
_MAX_REGISTRY_BYTES = 30 * 1024 * 1024
_MAX_REGISTRY_EXPANDED_BYTES = 100 * 1024 * 1024

# reprt_code -> (fiscal_period, period-end month/day). This assumes a
# calendar (December) fiscal year - true for the large majority of KOSPI/
# KOSDAQ filers; DART's disclosure-list and financial-statement endpoints
# don't expose the filer's actual fiscal month.
REPORT_CODE_PERIOD: dict[str, tuple[str, int, int]] = {
    "11013": ("Q", 3, 31),
    "11012": ("H1", 6, 30),
    "11014": ("9M", 9, 30),
    "11011": ("FY", 12, 31),
}
_REPORT_NAME_MARKERS: tuple[tuple[str, str], ...] = (
    ("사업보고서", "annual"),
    ("반기보고서", "semiannual"),
    ("분기보고서", "quarterly"),
)
_PERIOD_PATTERN = re.compile(r"\((\d{4})\.(\d{2})\)")


class DartClient(RetryingClient):
    """Search DART filers and retrieve periodic-report metadata/documents."""

    def __init__(
        self,
        api_key: str = "",
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            "DART",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            client=client,
        )
        self._api_key = api_key.strip()
        self._companies: tuple[Company, ...] | None = None

    async def search_companies(self, query: str, *, limit: int = 10) -> list[Company]:
        companies = await self._company_registry()
        records = [
            ((company.ticker or "", company.source_id, company.name), company)
            for company in companies
        ]
        return ranked_matches(query.removeprefix("kr_dart_"), records, limit=limit)

    async def list_filings(
        self,
        company_id_or_code: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
    ) -> list[Filing]:
        corp_code = self.normalize_corp_code(company_id_or_code)
        body = await self._call_json(
            f"{API_BASE_URL}/list.json",
            {
                "corp_code": corp_code,
                "pblntf_ty": "A",
                "page_no": "1",
                "page_count": str(min(100, max(1, limit))),
            },
        )
        rows = body.get("list") or []
        if not isinstance(rows, list):
            raise SourceError("DART returned an invalid disclosure-list response.")
        company_id = f"kr_dart_{corp_code}"
        filings = [
            filing
            for row in rows
            if (filing := self._filing_from_row(company_id, row)) is not None
        ]
        filings.sort(key=lambda filing: filing.filing_date, reverse=True)
        return filings[: max(1, limit)]

    async def download_document(self, document_id: str) -> SourceDocument:
        rcept_no = document_id.strip()
        if not _RCEPT_NO.fullmatch(rcept_no):
            raise DocumentUnavailableError("Expected a 14-digit DART receipt number.")
        source_url = f"{API_BASE_URL}/document.xml?rcept_no={rcept_no}"
        data = await self._call_binary(
            f"{API_BASE_URL}/document.xml", {"rcept_no": rcept_no}
        )
        if not data:
            raise DocumentUnavailableError(f"DART filing {rcept_no} was empty.")
        if len(data) > MAX_TAGGED_DOCUMENT_BYTES:
            limit_mb = MAX_TAGGED_DOCUMENT_BYTES // 1024 // 1024
            raise DocumentUnavailableError(
                f"The DART filing package exceeds the {limit_mb} MB limit."
            )
        return SourceDocument(
            data=data, media_type="application/zip", source_url=source_url
        )

    async def financial_statements(
        self, corp_code: str, *, bsns_year: str, reprt_code: str, fs_div: str
    ) -> list[dict[str, Any]]:
        """Return raw fnlttSinglAcntAll.json rows - the single company's full
        set of IFRS-tagged financial-statement accounts for one fiscal
        period."""

        body = await self._call_json(
            f"{API_BASE_URL}/fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            },
        )
        rows = body.get("list") or []
        if not isinstance(rows, list):
            raise SourceError("DART returned an invalid financial-statement response.")
        return [row for row in rows if isinstance(row, dict)]

    def matches_company_id(self, value: str) -> bool:
        return value.casefold().startswith("kr_dart_")

    def matches_filing_id(self, value: str) -> bool:
        return value.casefold().startswith("kr_dart_filing_")

    @staticmethod
    def normalize_corp_code(value: str) -> str:
        code = value.strip()
        if code.casefold().startswith("kr_dart_"):
            code = code[len("kr_dart_") :]
        if not _CORP_CODE.fullmatch(code):
            raise SourceError("Expected a Korean company ID like kr_dart_00126380.")
        return code

    async def _call_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        response = await self._api_request(url, params)
        try:
            body = response.json()
        except ValueError as exc:
            raise SourceError("DART returned an invalid JSON response.") from exc
        if not isinstance(body, dict):
            raise SourceError("DART returned an invalid JSON response.")
        status = str(body.get("status", "")).strip()
        if status == "013":  # No matching data - not an error condition.
            return {"list": []}
        if status and status != "000":
            raise SourceError(
                f"DART returned {status}: {body.get('message', 'unknown error')}"
            )
        return body

    async def _call_binary(self, url: str, params: dict[str, str]) -> bytes:
        response = await self._api_request(url, params)
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type.strip().casefold() == "application/json":
            try:
                body = response.json()
            except ValueError:
                body = {}
            message = body.get("message") if isinstance(body, dict) else None
            raise DocumentUnavailableError(
                f"DART did not return a file: {message or 'unknown error'}"
            )
        return response.content

    async def _api_request(self, url: str, params: dict[str, str]) -> httpx.Response:
        if not self._api_key:
            raise ConfigurationError(
                "DART_API_KEY is required for Korean company search, filing "
                "history, and financial statements."
            )
        request_params = dict(params)
        request_params["crtfc_key"] = self._api_key
        return await self._request("GET", url, params=request_params)

    async def _company_registry(self) -> tuple[Company, ...]:
        if self._companies is not None:
            return self._companies
        data = await self._call_binary(f"{API_BASE_URL}/corpCode.xml", {})
        if len(data) > _MAX_REGISTRY_BYTES:
            raise SourceError("The DART corp-code archive is unexpectedly large.")
        self._companies = tuple(self._parse_corp_code_archive(data))
        return self._companies

    @classmethod
    def _parse_corp_code_archive(cls, archive_bytes: bytes) -> list[Company]:
        try:
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                members = [
                    member for member in archive.infolist() if not member.is_dir()
                ]
                if len(members) != 1:
                    raise SourceError(
                        "The DART corp-code archive has an unexpected file count."
                    )
                member = members[0]
                if member.file_size > _MAX_REGISTRY_EXPANDED_BYTES:
                    raise SourceError("The DART corp-code XML is unexpectedly large.")
                xml_bytes = archive.read(member)
        except SourceError:
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            raise SourceError(
                f"Could not read the DART corp-code archive: {exc}"
            ) from exc
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise SourceError(
                f"The DART corp-code archive XML could not be parsed: {exc}"
            ) from exc

        companies: list[Company] = []
        for element in root.findall("list"):
            corp_code = (element.findtext("corp_code") or "").strip()
            name = (element.findtext("corp_name") or "").strip()
            stock_code = (element.findtext("stock_code") or "").strip()
            if not _CORP_CODE.fullmatch(corp_code) or not name or not stock_code:
                continue
            companies.append(
                Company(
                    id=f"kr_dart_{corp_code}",
                    source_id=corp_code,
                    name=name,
                    sources=("dart",),
                    market="KR",
                    country_code="KR",
                    ticker=stock_code,
                    local_code=corp_code,
                    status="active listed equity issuer",
                    source_url=f"{API_BASE_URL}/company.json?corp_code={corp_code}",
                )
            )
        return companies

    def _filing_from_row(self, company_id: str, row: object) -> Filing | None:
        if not isinstance(row, dict):
            return None
        rcept_no = str(row.get("rcept_no", "")).strip()
        if not _RCEPT_NO.fullmatch(rcept_no):
            return None
        report_nm = str(row.get("report_nm", "")).strip()
        filing_type = next(
            (
                filing_type
                for marker, filing_type in _REPORT_NAME_MARKERS
                if marker in report_nm
            ),
            None,
        )
        if filing_type is None:
            return None
        filed = self._parse_date(row.get("rcept_dt"))
        if filed is None:
            return None
        return Filing(
            id=f"kr_dart_filing_{rcept_no}",
            company_id=company_id,
            source="dart",
            source_id=rcept_no,
            title=report_nm or filing_type,
            category="accounts",
            filing_type=filing_type,
            filing_date=filed,
            published_at=utc_midnight(filed),
            period_end=self._period_end(report_nm),
            document_id=rcept_no,
            media_type="application/zip",
            issuer_name=str(row.get("corp_name", "")).strip() or "Unknown filer",
            language="ko",
            source_url=f"{API_BASE_URL}/document.xml?rcept_no={rcept_no}",
        )

    @staticmethod
    def _period_end(report_nm: str) -> date | None:
        match = _PERIOD_PATTERN.search(report_nm)
        if match is None:
            return None
        year, month = int(match.group(1)), int(match.group(2))
        for _, period_month, period_day in REPORT_CODE_PERIOD.values():
            if period_month == month:
                try:
                    return date(year, period_month, period_day)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _parse_date(value: object) -> date | None:
        try:
            return datetime.strptime(str(value).strip(), "%Y%m%d").date()
        except ValueError:
            return None
