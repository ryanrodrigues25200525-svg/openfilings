"""Official Mexican Stock Exchange (BMV) public-filings client."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from openfilings.adapters._common import RetryingClient, ranked_matches
from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing, SourceName

ISSUER_PAGE = "https://www.bmv.com.mx/en/issuers/issuers-information"
SEARCH_URL = (
    "https://www.bmv.com.mx/en/Grupo_BMV/Informacion_de_emisora/"
    "_rid/541/_mto/3/_mod/doSearch"
)
FINANCIAL_PAGE = (
    "https://www.bmv.com.mx/en/issuers/financialinformation/"
    "{ticker}-{issuer_id}-CGEN_CAPIT"
)

_DOCUMENT_HOST = "www.bmv.com.mx"
_DOCUMENT_PATHS = ("/docs-pub/ifrsxbrl/", "/docs-pub/infoanua/")
_DATE_FORMAT = "%d-%b-%Y %H:%M"


class BmvClient(RetryingClient):
    """Search BMV equity issuers and retrieve annual or quarterly reports."""

    source: SourceName = "bmv"

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            "BMV",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            headers={"User-Agent": "openfilings/0.20", "Accept": "*/*"},
            client=client,
        )
        self._companies: tuple[Company, ...] | None = None

    async def search_companies(self, query: str, *, limit: int = 10) -> list[Company]:
        companies = await self._issuer_registry()
        records = [
            ((company.ticker or "", company.name), company) for company in companies
        ]
        return ranked_matches(query.removeprefix("mx_bmv_"), records, limit=limit)

    async def list_filings(
        self,
        company_id: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
    ) -> list[Filing]:
        issuer_id = self._issuer_id(company_id)
        company = next(
            (
                item
                for item in await self._issuer_registry()
                if item.source_id == issuer_id
            ),
            None,
        )
        if company is None or not company.ticker:
            raise SourceError(f"BMV issuer {issuer_id} is not an active equity issuer.")
        page_url = FINANCIAL_PAGE.format(
            ticker=company.ticker,
            issuer_id=issuer_id,
        )
        response = await self._request("GET", page_url)
        parser = _FilingTableParser()
        parser.feed(response.text)
        filings = [
            filing
            for row in parser.rows
            if (filing := self._filing_from_row(company, row)) is not None
        ]
        filings.sort(key=lambda filing: filing.published_at, reverse=True)
        return filings[: max(1, limit)]

    async def download_document(self, document_id: str) -> SourceDocument:
        source_url = self.document_url(document_id)
        response = await self._request("GET", source_url)
        data = response.content
        if not data:
            raise DocumentUnavailableError("The BMV filing document was empty.")
        if len(data) > MAX_TAGGED_DOCUMENT_BYTES:
            raise DocumentUnavailableError("The BMV filing exceeds the 150 MB limit.")
        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        if data.startswith(b"PK\x03\x04"):
            media_type = "application/zip"
        elif data.startswith(b"%PDF"):
            media_type = "application/pdf"
        elif media_type.startswith("text/html"):
            raise DocumentUnavailableError(
                "BMV returned an HTML error page instead of a filing."
            )
        return SourceDocument(
            data=data,
            media_type=media_type,
            source_url=source_url,
            profile="bmv-json" if media_type == "application/zip" else None,
        )

    def matches_company_id(self, value: str) -> bool:
        return value.casefold().startswith("mx_bmv_")

    def matches_filing_id(self, value: str) -> bool:
        return value.casefold().startswith("mx_bmv_filing_")

    @staticmethod
    def document_url(value: str) -> str:
        url = value.strip()
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != _DOCUMENT_HOST
            or ".." in parsed.path
            or not parsed.path.casefold().startswith(_DOCUMENT_PATHS)
            or not parsed.path.casefold().endswith((".pdf", ".zip"))
        ):
            raise DocumentUnavailableError("Unsafe BMV document URL.")
        return url

    async def _issuer_registry(self) -> tuple[Company, ...]:
        if self._companies is not None:
            return self._companies
        await self._request("GET", ISSUER_PAGE)
        response = await self._request(
            "GET",
            SEARCH_URL,
            params={
                "idTipoMercado": "CGEN_CAPIT",
                "idTipoInstrumento": "CGEN_ELAC",
                "idTipoEmpresa": "",
                "idSector": "",
                "idSubsector": "",
                "idRamo": "",
                "idSubramo": "",
            },
            headers={
                "Referer": ISSUER_PAGE,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        payload = self._json_payload(response.text)
        rows = payload.get("response", {}).get("resultado", [])
        if not isinstance(rows, list):
            raise SourceError("BMV returned an invalid issuer registry.")
        companies = [
            company for row in rows if (company := self._company_from_row(row))
        ]
        self._companies = tuple(companies)
        return self._companies

    @staticmethod
    def _json_payload(text: str) -> dict[str, Any]:
        clean = text.strip()
        if clean.startswith("for(;;);(") and clean.endswith(")"):
            clean = clean[len("for(;;);(") : -1]
        try:
            payload = json.loads(clean)
        except json.JSONDecodeError as exc:
            raise SourceError("BMV returned an invalid issuer response.") from exc
        if not isinstance(payload, dict):
            raise SourceError("BMV returned an invalid issuer response.")
        return payload

    @staticmethod
    def _company_from_row(row: Any) -> Company | None:
        if not isinstance(row, dict):
            return None
        issuer_id = str(row.get("idEmisora", "")).strip()
        ticker = str(row.get("claveEmisora", "")).strip().upper()
        name = str(row.get("razonSocial", "")).strip()
        if not issuer_id.isdigit() or not ticker or not name:
            return None
        return Company(
            id=f"mx_bmv_{issuer_id}",
            source_id=issuer_id,
            name=name,
            sources=("bmv",),
            market="MX",
            country_code="MX",
            ticker=ticker,
            local_code=ticker,
            status="active listed equity issuer",
            company_type="BMV equity issuer",
            source_url=f"https://www.bmv.com.mx/en/issuers/profile/{ticker}-{issuer_id}",
        )

    def _filing_from_row(self, company: Company, row: _FilingRow) -> Filing | None:
        title = " ".join(row.text.split())
        lowered = title.casefold()
        if not row.href or not (
            "información del trimestre" in lowered
            or "informacion del trimestre" in lowered
            or "informe anual en formato pdf" in lowered
        ):
            return None
        try:
            source_url = self._resolve_document_url(row.href)
            published_at = datetime.strptime(row.date_text, _DATE_FORMAT).replace(
                tzinfo=UTC
            )
        except (DocumentUnavailableError, ValueError):
            return None
        source_id_match = re.search(r"_(\d+)_", urlparse(source_url).path)
        if source_id_match is None:
            return None
        source_id = source_id_match.group(1)
        year_match = re.search(r"\b(20\d{2})\b", title)
        period_end = None
        filing_type = "annual"
        if "trimestre" in lowered:
            filing_type = "quarterly"
            quarter_match = re.search(r"trimestre\s+(\d)", lowered)
            if year_match and quarter_match:
                month = int(quarter_match.group(1)) * 3
                day = 31 if month in {3, 12} else 30
                period_end = datetime(int(year_match.group(1)), month, day).date()
        elif year_match:
            period_end = datetime(int(year_match.group(1)), 12, 31).date()
        media_type = (
            "application/zip" if source_url.endswith(".zip") else "application/pdf"
        )
        return Filing(
            id=f"mx_bmv_filing_{source_id}",
            company_id=company.id,
            source="bmv",
            source_id=source_id,
            title=title,
            category="accounts",
            filing_type=filing_type,
            filing_date=published_at.date(),
            published_at=published_at,
            period_end=period_end,
            document_id=source_url,
            media_type=media_type,
            issuer_name=company.name,
            language="es",
            xbrl_available=media_type == "application/zip",
            pdf_available=media_type == "application/pdf",
            source_url=source_url,
        )

    @classmethod
    def _resolve_document_url(cls, href: str) -> str:
        parsed = urlparse(href)
        if parsed.path.endswith("/visor/visorXbrl.html"):
            target = parse_qs(parsed.query).get("docins", [""])[0]
            filename = target.rsplit("/", 1)[-1]
            href = f"/docs-pub/ifrsxbrl/{filename}"
        return cls.document_url(urljoin("https://www.bmv.com.mx", href))

    @staticmethod
    def _issuer_id(value: str) -> str:
        clean = value.strip()
        if clean.casefold().startswith("mx_bmv_"):
            clean = clean[len("mx_bmv_") :]
        if not clean.isdigit():
            raise SourceError("Expected a Mexican company ID shaped like mx_bmv_6024.")
        return clean


class _FilingRow:
    def __init__(self, date_text: str, text: str, href: str | None) -> None:
        self.date_text = date_text
        self.text = text
        self.href = href


class _FilingTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_FilingRow] = []
        self._in_row = False
        self._cells: list[str] = []
        self._cell_parts: list[str] | None = None
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self._in_row = True
            self._cells = []
            self._href = None
        elif tag == "td" and self._in_row:
            self._cell_parts = []
        elif tag == "a" and self._in_row:
            href = attributes.get("href")
            if href and "/docs-pub/" in href:
                self._href = href

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._cell_parts is not None:
            self._cells.append(" ".join("".join(self._cell_parts).split()))
            self._cell_parts = None
        elif tag == "tr" and self._in_row:
            if len(self._cells) >= 2:
                self.rows.append(_FilingRow(self._cells[0], self._cells[1], self._href))
            self._in_row = False
