"""Official KAP (Kamuyu Aydinlatma Platformu - Turkey's Public Disclosure
Platform) client for BIST-listed companies.

KAP's own Rest API data-distribution service is a paid, contract-gated
product (a Borsa İstanbul data-distribution agreement is required). This
client instead calls the plain JSON endpoints that KAP's own public website
(a Next.js SPA) uses to render kap.org.tr - no authentication, no API key,
and no bot-detection bypass involved: normal GET/POST requests with a
Referer header return 200 with the same data a browser would load.
"Finansal Rapor" (financial report) disclosures embed the filer's full
IFRS-tagged financial statements as pre-rendered HTML viewer tables (see
xbrl/kap_structured.py) rather than a downloadable raw XBRL instance.
"""

from __future__ import annotations

import re
import struct
from datetime import UTC, date, datetime, timedelta

import httpx

from openfilings.adapters._common import RetryingClient, normalize_text, ranked_matches
from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing, SourceName

BASE_URL = "https://www.kap.org.tr"
COMPANY_LIST_URL = f"{BASE_URL}/tr/api/company/items/IGS/A"
DISCLOSURE_LIST_URL = f"{BASE_URL}/tr/api/disclosure/members/byCriteria"
DISCLOSURE_DETAIL_URL = f"{BASE_URL}/tr/api/notification/attachment-detail"
FILE_DOWNLOAD_URL = f"{BASE_URL}/tr/api/file/download"
SEARCH_LANDING_URL = f"{BASE_URL}/tr/bildirim-sorgu"

_OID_PATTERN = re.compile(r"^[0-9a-f]{20,40}$")
_DISCLOSURE_INDEX_PATTERN = re.compile(r"^\d{1,10}$")
# The disclosure-list endpoint rejects any fromDate/toDate span over 365
# days (HTTP 400), even when filtered to one company - page backwards in
# year-long windows instead of requesting one wide range.
_WINDOW_DAYS = 365
_LOOKBACK_WINDOWS = 3
_FINANCIAL_REPORT_SUBJECT = "Finansal Rapor"
_CATEGORY_CODES: dict[str, tuple[str, str]] = {
    "accounts": ("FR", "FR"),
    "material_event": ("ODA", "ODA"),
    "corporate_action": ("", "CA"),
}
_JAVA_ARRAY_MAGIC = b"\xac\xed\x00\x05"
# The disclosure's own "period" field (1-4) names the reporting quarter;
# KAP has no fiscal-year-end other than the calendar year for BIST filers.
_PERIOD_END_MONTH_DAY: dict[int, tuple[int, int]] = {
    1: (3, 31),
    2: (6, 30),
    3: (9, 30),
    4: (12, 31),
}


class KapClient(RetryingClient):
    """Search BIST-listed issuers and read KAP's public disclosure feed."""

    source: SourceName = "kap"

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            "KAP",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            headers={"Referer": SEARCH_LANDING_URL},
            client=client,
        )
        self._companies: tuple[Company, ...] | None = None

    async def search_companies(self, query: str, *, limit: int = 10) -> list[Company]:
        companies = await self._company_registry()
        records = [
            ((company.ticker or "", company.source_id, company.name), company)
            for company in companies
        ]
        return ranked_matches(query.removeprefix("tr_kap_"), records, limit=limit)

    async def list_filings(
        self,
        company_id: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
    ) -> list[Filing]:
        oid = self.normalize_company_oid(company_id)
        company_id_norm = f"tr_kap_{oid}"
        normalized_category = category.casefold() if category else None
        if normalized_category not in {
            None,
            "accounts",
            "disclosure",
            "material_event",
            "corporate_action",
        }:
            return []
        requested_class, requested_type = _CATEGORY_CODES.get(
            normalized_category or "", ("", "")
        )
        window_end = date.today()
        filings: list[Filing] = []
        for _ in range(_LOOKBACK_WINDOWS):
            window_start = window_end - timedelta(days=_WINDOW_DAYS)
            response = await self._request(
                "POST",
                DISCLOSURE_LIST_URL,
                json={
                    "fromDate": window_start.isoformat(),
                    "toDate": window_end.isoformat(),
                    "mkkMemberOidList": [oid],
                    "disclosureClass": requested_class,
                    "subjectList": [],
                },
            )
            try:
                rows = response.json()
            except ValueError as exc:
                raise SourceError(
                    "KAP returned an invalid disclosure response."
                ) from exc
            if not isinstance(rows, list):
                raise SourceError("KAP returned an invalid disclosure response.")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                is_financial_report = (
                    row.get("subject") == _FINANCIAL_REPORT_SUBJECT
                    and row.get("disclosureType") == "FR"
                )
                if normalized_category == "accounts" and not is_financial_report:
                    continue
                if (
                    normalized_category == "material_event"
                    and row.get("disclosureType") != requested_type
                ):
                    continue
                if (
                    normalized_category == "corporate_action"
                    and row.get("disclosureType") != requested_type
                ):
                    continue
                filing = self._filing_from_row(
                    company_id_norm,
                    row,
                    is_financial_report=is_financial_report,
                    category=normalized_category,
                )
                if filing is not None:
                    filings.append(filing)
            window_end = window_start - timedelta(days=1)

        deduped = {filing.id: filing for filing in filings}
        ordered = sorted(
            deduped.values(),
            key=lambda filing: filing.published_at or filing.filing_date,
            reverse=True,
        )
        return ordered[: max(1, limit)]

    async def download_document(self, document_id: str) -> SourceDocument:
        index = document_id.strip()
        if not _DISCLOSURE_INDEX_PATTERN.fullmatch(index):
            raise DocumentUnavailableError("Expected a numeric KAP disclosure index.")
        detail = await self._disclosure_detail(index)
        attachments = detail.get("attachments")
        obj_id = (
            str(attachments[0].get("objId", "")).strip()
            if isinstance(attachments, list)
            and attachments
            and isinstance(attachments[0], dict)
            else ""
        )
        if not _OID_PATTERN.fullmatch(obj_id.casefold()):
            raise DocumentUnavailableError(
                "The KAP disclosure has no usable attachment."
            )
        source_url = f"{FILE_DOWNLOAD_URL}/{obj_id}"
        response = await self._request(
            "GET",
            source_url,
            headers={"Referer": f"{BASE_URL}/tr/Bildirim/{index}"},
        )
        pdf_bytes = self._unwrap_java_byte_array(response.content)
        if not pdf_bytes:
            raise DocumentUnavailableError("The KAP attachment was empty.")
        if len(pdf_bytes) > MAX_TAGGED_DOCUMENT_BYTES:
            raise DocumentUnavailableError(
                "The KAP attachment exceeds the 150 MB limit."
            )
        return SourceDocument(
            data=pdf_bytes, media_type="application/pdf", source_url=source_url
        )

    async def financial_report_bodies(self, disclosure_index: str) -> list[str]:
        """Return the per-statement HTML viewer tables for one "Finansal
        Rapor" disclosure, for xbrl/kap_structured.py to parse."""

        index = str(disclosure_index).strip()
        if not _DISCLOSURE_INDEX_PATTERN.fullmatch(index):
            raise SourceError("Expected a numeric KAP disclosure index.")
        detail = await self._disclosure_detail(index)
        bodies = detail.get("disclosureBody")
        if not isinstance(bodies, list):
            return []
        return [item for item in bodies if isinstance(item, str)]

    def matches_company_id(self, value: str) -> bool:
        clean = value.casefold()
        return clean.startswith("tr_kap_") and bool(
            _OID_PATTERN.fullmatch(clean[len("tr_kap_") :])
        )

    def matches_filing_id(self, value: str) -> bool:
        clean = value.casefold()
        return clean.startswith("tr_kap_") and clean[len("tr_kap_") :].isdigit()

    @staticmethod
    def normalize_company_oid(value: str) -> str:
        clean = value.strip()
        if clean.casefold().startswith("tr_kap_"):
            clean = clean[len("tr_kap_") :]
        clean = clean.casefold()
        if not _OID_PATTERN.fullmatch(clean):
            raise SourceError(
                "Expected a Turkish company ID like tr_kap_{mkkMemberOid}."
            )
        return clean

    async def _disclosure_detail(self, index: str) -> dict[str, object]:
        response = await self._request(
            "GET",
            f"{DISCLOSURE_DETAIL_URL}/{index}",
            headers={"Referer": f"{BASE_URL}/tr/Bildirim/{index}"},
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise SourceError(
                "KAP returned an invalid disclosure-detail response."
            ) from exc
        if not isinstance(body, list) or not body or not isinstance(body[0], dict):
            raise SourceError("KAP returned an invalid disclosure-detail response.")
        return body[0]

    async def _company_registry(self) -> tuple[Company, ...]:
        if self._companies is not None:
            return self._companies
        response = await self._request("GET", COMPANY_LIST_URL)
        try:
            rows = response.json()
        except ValueError as exc:
            raise SourceError("KAP returned an invalid company-list response.") from exc
        if not isinstance(rows, list):
            raise SourceError("KAP returned an invalid company-list response.")
        self._companies = tuple(
            company
            for row in rows
            if (company := self._company_from_row(row)) is not None
        )
        return self._companies

    @staticmethod
    def _company_from_row(row: object) -> Company | None:
        if not isinstance(row, dict):
            return None
        oid = _text(row.get("mkkMemberOid")).casefold()
        ticker = _text(row.get("stockCode")).upper()
        title = _text(row.get("kapMemberTitle"))
        company_code = _text(row.get("companyCode"))
        if not _OID_PATTERN.fullmatch(oid) or not ticker or not title:
            return None
        source_url = (
            f"{BASE_URL}/en/sirket-bilgileri/genel/{company_code}-{_slug(title)}"
            if company_code
            else SEARCH_LANDING_URL
        )
        return Company(
            id=f"tr_kap_{oid}",
            source_id=oid,
            name=title,
            sources=("kap",),
            market="TR",
            country_code="TR",
            ticker=ticker,
            local_code=ticker,
            status="active listed equity issuer",
            company_type="BIST-listed company",
            source_url=source_url,
        )

    def _filing_from_row(
        self,
        company_id: str,
        row: dict[str, object],
        *,
        is_financial_report: bool,
        category: str | None,
    ) -> Filing | None:
        index = row.get("disclosureIndex")
        if not isinstance(index, int):
            return None
        published_at = self._parse_datetime(row.get("publishDate"))
        if published_at is None:
            return None
        summary = _text(row.get("summary"))
        subject = _text(row.get("subject"))
        period_end = _period_end(row) if is_financial_report else None
        has_attachment = bool(row.get("attachmentCount"))
        filing_category = (
            "accounts"
            if is_financial_report
            else category
            if category in {"material_event", "corporate_action"}
            else "disclosure"
        )
        filing_type = (
            "financial_report"
            if is_financial_report
            else _text(row.get("disclosureType")).casefold()
            if category in {"material_event", "corporate_action"}
            else "disclosure"
        )
        return Filing(
            id=f"tr_kap_{index}",
            company_id=company_id,
            source="kap",
            source_id=str(index),
            title=summary or subject or "KAP disclosure",
            category=filing_category,
            filing_type=filing_type,
            filing_date=published_at.date(),
            published_at=published_at,
            period_end=period_end,
            document_id=str(index) if has_attachment else None,
            media_type="application/pdf" if has_attachment else None,
            issuer_name=_text(row.get("kapTitle")) or "Unknown issuer",
            language="tr",
            xbrl_available=is_financial_report,
            pdf_available=has_attachment,
            source_url=f"{BASE_URL}/en/Bildirim/{index}",
        )

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        try:
            return datetime.strptime(str(value).strip(), "%d.%m.%Y %H:%M:%S").replace(
                tzinfo=UTC
            )
        except ValueError:
            return None

    @staticmethod
    def _unwrap_java_byte_array(data: bytes) -> bytes:
        """KAP's file-download endpoint wraps the raw file in a Java-
        serialized byte[] (magic bytes + a TC_ARRAY/TC_CLASSDESC header for
        "[B", then a big-endian length, then the payload)."""

        if not data.startswith(_JAVA_ARRAY_MAGIC):
            return data
        marker = data.find(b"\x78\x70", 10)
        if marker == -1 or len(data) < marker + 6:
            raise DocumentUnavailableError("Could not unwrap the KAP attachment.")
        length = struct.unpack(">I", data[marker + 2 : marker + 6])[0]
        payload = data[marker + 6 : marker + 6 + length]
        if len(payload) != length:
            raise DocumentUnavailableError("The KAP attachment was truncated.")
        return payload


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _slug(title: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", normalize_text(title)))


def _period_end(row: dict[str, object]) -> date | None:
    year = row.get("year")
    period = row.get("period")
    if not isinstance(year, int) or not isinstance(period, int):
        return None
    month_day = _PERIOD_END_MONTH_DAY.get(period)
    if month_day is None:
        return None
    try:
        return date(year, *month_day)
    except ValueError:
        return None
