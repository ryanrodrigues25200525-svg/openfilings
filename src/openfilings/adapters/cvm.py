"""Official CVM public-data client for Brazilian exchange-listed issuers."""

from __future__ import annotations

import asyncio
import csv
import io
import re
import unicodedata
import zipfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing

COMPANY_REGISTRY_URL = (
    "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
)
IPE_ARCHIVE_URL = (
    "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{year}.zip"
)
COMPANY_DATASET_URL = "https://dados.cvm.gov.br/dataset/cia_aberta-cad"

_CVM_CODE_PATTERN = re.compile(r"^\d{1,6}$")
_FILING_ID_PATTERN = re.compile(r"^\d+$")
_MAX_REGISTRY_BYTES = 10 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 30 * 1024 * 1024
_MAX_ARCHIVE_EXPANDED_BYTES = 150 * 1024 * 1024
_FINANCIAL_TYPES = {
    "demonstracoes financeiras anuais completas": "annual",
    "demonstracoes financeiras intermediarias": "interim",
    "demonstracoes financeiras adicionais": "additional_financial_statements",
}


class CvmClient:
    """Search CVM issuers and retrieve public IPE filing documents."""

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
        self._history_years = max(1, min(history_years, 5))
        self._today = today
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": "openfilings/0.15"},
        )
        self._companies: tuple[Company, ...] | None = None

    async def __aenter__(self) -> CvmClient:
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
        normalized = self._normalize_search(clean_query.removeprefix("br_cvm_"))
        ranked: list[tuple[int, str, Company]] = []
        for company in companies:
            fields = (
                company.local_code or "",
                company.source_id,
                company.name,
                company.english_name or "",
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
        code = self.normalize_cvm_code(company_id_or_code)
        companies = await self._company_registry()
        if not any(company.local_code == code for company in companies):
            raise SourceError(
                f"CVM code {code} is not an active Brazilian exchange-listed issuer."
            )
        filings: dict[str, Filing] = {}
        current_year = self._today().year
        for year in range(current_year, current_year - self._history_years, -1):
            for row in await self._ipe_rows(year):
                if self._format_cvm_code(row.get("Codigo_CVM", "")) != code:
                    continue
                filing = self._filing_from_row(row, category=category)
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

    async def download_document(self, document_url: str) -> SourceDocument:
        source_url = self.document_url(document_url)
        response = await self._request("GET", source_url)
        if not response.content:
            raise DocumentUnavailableError("The CVM filing document was empty.")
        if len(response.content) > MAX_TAGGED_DOCUMENT_BYTES:
            raise DocumentUnavailableError("The CVM filing exceeds the 150 MB limit.")

        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        data = response.content
        if data.startswith(b"PK\x03\x04"):
            data = self._pdf_from_archive(data)
            media_type = "application/pdf"
        elif data.startswith(b"%PDF"):
            media_type = "application/pdf"
        elif media_type in {"text/html", "application/xhtml+xml"}:
            raise DocumentUnavailableError(
                "CVM returned an HTML error page instead of the filing document."
            )
        return SourceDocument(
            data=data,
            media_type=media_type or "application/pdf",
            source_url=source_url,
        )

    def matches_company_id(self, value: str) -> bool:
        return value.casefold().startswith("br_cvm_")

    def matches_filing_id(self, value: str) -> bool:
        return value.casefold().startswith("br_cvm_")

    @staticmethod
    def normalize_cvm_code(value: str) -> str:
        clean_value = value.strip()
        if clean_value.casefold().startswith("br_cvm_"):
            clean_value = clean_value[len("br_cvm_") :]
        if not _CVM_CODE_PATTERN.fullmatch(clean_value):
            raise SourceError(
                "Expected a CVM company ID shaped like br_cvm_{numeric_code}."
            )
        return clean_value.zfill(6)

    @staticmethod
    def document_url(value: str) -> str:
        url = value.strip()
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        if (
            parsed.scheme != "https"
            or parsed.netloc.casefold() != "www.rad.cvm.gov.br"
            or parsed.path.casefold() != "/enet/frmdownloaddocumento.aspx"
            or query.get("descTipo") != ["IPE"]
            or not query.get("numSequencia", [""])[0].isdigit()
        ):
            raise DocumentUnavailableError("Unsafe CVM document URL.")
        return url

    async def _company_registry(self) -> tuple[Company, ...]:
        if self._companies is not None:
            return self._companies
        response = await self._request("GET", COMPANY_REGISTRY_URL)
        if len(response.content) > _MAX_REGISTRY_BYTES:
            raise SourceError("The CVM company registry is unexpectedly large.")
        rows = self._csv_rows(response.content)
        companies: dict[str, Company] = {}
        for row in rows:
            company = self._company_from_row(row)
            if company is not None:
                companies[company.id] = company
        self._companies = tuple(companies.values())
        return self._companies

    async def _ipe_rows(self, year: int) -> list[dict[str, str]]:
        response = await self._request(
            "GET", IPE_ARCHIVE_URL.format(year=year), allow_not_found=True
        )
        if response.status_code == 404:
            return []
        if len(response.content) > _MAX_ARCHIVE_BYTES:
            raise SourceError(f"The CVM IPE archive for {year} is unexpectedly large.")
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                members = [
                    member
                    for member in archive.infolist()
                    if not member.is_dir()
                    and member.filename.casefold().endswith(".csv")
                ]
                if len(members) != 1:
                    raise SourceError(
                        f"The CVM IPE archive for {year} has unexpected contents."
                    )
                member = members[0]
                if member.file_size > _MAX_ARCHIVE_EXPANDED_BYTES:
                    raise SourceError(
                        f"The CVM IPE archive for {year} expands beyond the safe limit."
                    )
                return self._csv_rows(archive.read(member))
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise SourceError(f"The CVM IPE archive for {year} is invalid.") from exc

    def _company_from_row(self, row: dict[str, str]) -> Company | None:
        if (
            self._normalize_search(row.get("SIT", "")) != "ativo"
            or self._normalize_search(row.get("TP_MERC", "")) != "bolsa"
            or self._normalize_search(row.get("SIT_EMISSOR", "")) != "fase operacional"
        ):
            return None
        name = row.get("DENOM_SOCIAL", "").strip()
        cnpj = row.get("CNPJ_CIA", "").strip()
        code = self._format_cvm_code(row.get("CD_CVM", ""))
        if not name or not cnpj or code is None:
            return None
        address = ", ".join(
            value.strip()
            for value in (
                row.get("LOGRADOURO", ""),
                row.get("COMPL", ""),
                row.get("BAIRRO", ""),
                row.get("MUN", ""),
                row.get("UF", ""),
            )
            if value.strip()
        )
        return Company(
            id=f"br_cvm_{code}",
            source_id=cnpj,
            name=name,
            sources=("cvm",),
            market="BR",
            country_code="BR",
            local_code=code,
            english_name=row.get("DENOM_COMERC", "").strip() or None,
            status="active listed issuer",
            company_type=row.get("CATEG_REG", "").strip() or "CVM issuer",
            address=address or None,
            source_url=COMPANY_DATASET_URL,
        )

    def _filing_from_row(
        self, row: dict[str, str], *, category: str | None
    ) -> Filing | None:
        source_category = row.get("Categoria", "").strip()
        source_type = row.get("Tipo", "").strip()
        normalized_type = self._normalize_search(source_type)
        filing_type = _FINANCIAL_TYPES.get(normalized_type)
        if category and category.casefold() == "accounts" and filing_type is None:
            return None
        if (
            category
            and category.casefold() != "accounts"
            and self._normalize_search(category)
            != self._normalize_search(source_category)
        ):
            return None
        if filing_type is None:
            filing_type = self._slug(source_type or source_category or "document")

        code = self._format_cvm_code(row.get("Codigo_CVM", ""))
        source_url = row.get("Link_Download", "").strip()
        period_end = self._parse_date(row.get("Data_Referencia"))
        filed_on = self._parse_date(row.get("Data_Entrega"))
        if code is None or period_end is None or filed_on is None:
            return None
        try:
            source_url = self.document_url(source_url)
        except DocumentUnavailableError:
            return None
        sequence = parse_qs(urlparse(source_url).query)["numSequencia"][0]
        if not _FILING_ID_PATTERN.fullmatch(sequence):
            return None
        issuer_name = row.get("Nome_Companhia", "").strip() or "Unknown issuer"
        subject = row.get("Assunto", "").strip()
        title = (
            f"{source_type} for period ended {period_end.isoformat()}"
            if source_type
            else f"CVM filing for period ended {period_end.isoformat()}"
        )
        return Filing(
            id=f"br_cvm_{sequence}",
            company_id=f"br_cvm_{code}",
            source="cvm",
            source_id=sequence,
            title=title,
            category="accounts"
            if normalized_type in _FINANCIAL_TYPES
            else self._slug(source_category),
            filing_type=filing_type,
            filing_date=filed_on,
            published_at=self._date_time(filed_on),
            period_end=period_end,
            description=subject or None,
            document_id=source_url,
            media_type="application/pdf",
            issuer_name=issuer_name,
            language="pt",
            pdf_available=True,
            source_url=source_url,
        )

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        allow_not_found = bool(kwargs.pop("allow_not_found", False))
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                if attempt >= self._max_retries:
                    raise SourceError(f"CVM request failed: {exc}") from exc
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            if response.status_code == 404 and allow_not_found:
                return response
            if response.status_code not in {429, 500, 502, 503, 504}:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise SourceError(
                        f"CVM returned {response.status_code}: "
                        f"{response.text.strip()[:300] or response.reason_phrase}"
                    ) from exc
                return response
            if attempt >= self._max_retries:
                raise SourceError(
                    f"CVM returned {response.status_code}: "
                    f"{response.text.strip()[:300] or response.reason_phrase}"
                )
            await asyncio.sleep(self._retry_delay(response, attempt))
        raise AssertionError("retry loop exited unexpectedly")

    @staticmethod
    def _csv_rows(data: bytes) -> list[dict[str, str]]:
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise SourceError("CVM returned invalid CSV encoding.") from exc
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        if not reader.fieldnames:
            raise SourceError("CVM returned an empty CSV file.")
        return [dict(row) for row in reader]

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
                        "The CVM filing archive does not contain one safe PDF."
                    )
                return archive.read(members[0])
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise DocumentUnavailableError(
                "The CVM filing archive is invalid."
            ) from exc

    @staticmethod
    def _format_cvm_code(value: str) -> str | None:
        clean_value = value.strip()
        return (
            clean_value.zfill(6) if _CVM_CODE_PATTERN.fullmatch(clean_value) else None
        )

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
    def _slug(value: str) -> str:
        return (
            "_".join(re.findall(r"[a-z0-9]+", CvmClient._normalize_search(value)))
            or "document"
        )

    @staticmethod
    def _parse_date(value: str | None) -> date | None:
        try:
            return date.fromisoformat(value or "")
        except ValueError:
            return None

    @staticmethod
    def _date_time(value: date) -> datetime:
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC)

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        try:
            return min(float(retry_after), 30.0) if retry_after else 0.5 * (2**attempt)
        except ValueError:
            return 0.5 * (2**attempt)
