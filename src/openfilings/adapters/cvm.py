"""Official CVM public-data client for Brazilian exchange-listed issuers."""

from __future__ import annotations

import asyncio
import csv
import io
import re
import unicodedata
import zipfile
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES
from openfilings.models import Company, Filing, MajorHolderNotification

COMPANY_REGISTRY_URL = (
    "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
)
IPE_ARCHIVE_URL = (
    "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{year}.zip"
)
STRUCTURED_ARCHIVE_URL = (
    "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/{dataset_upper}/DADOS/"
    "{dataset}_cia_aberta_{year}.zip"
)
COMPANY_DATASET_URL = "https://dados.cvm.gov.br/dataset/cia_aberta-cad"

_CVM_CODE_PATTERN = re.compile(r"^\d{1,6}$")
_FILING_ID_PATTERN = re.compile(r"^\d+$")
_MAX_REGISTRY_BYTES = 10 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 30 * 1024 * 1024
_MAX_ARCHIVE_EXPANDED_BYTES = 150 * 1024 * 1024
_MAX_STRUCTURED_ARCHIVE_BYTES = 60 * 1024 * 1024
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
        self._structured_archives: dict[tuple[str, int], bytes | None] = {}

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
        # Insider trading and shareholding are one combined disclosure under
        # CVM Instrucao 358 art. 11 ("Valores Mobiliarios Negociados e
        # Detidos"), published as its own yearly Open Data archive - not
        # part of the general IPE archive the other categories come from.
        row_source = self._vlmo_rows if category == "insider" else self._ipe_rows
        filings: dict[str, Filing] = {}
        current_year = self._today().year
        for year in range(current_year, current_year - self._history_years, -1):
            for row in await row_source(year):
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

    async def search_disclosures(
        self,
        keyword: str,
        *,
        limit: int = 25,
        years: int = 1,
    ) -> list[Filing]:
        """Full-text search across every issuer's disclosures for the most
        recent year(s), not scoped to one company. The yearly IPE archive
        already covers every issuer in one file, so this reuses it directly
        instead of a new endpoint - it just skips the per-company filter and
        matches the keyword against the subject/type/category fields."""

        clean_keyword = self._normalize_search(keyword)
        if not clean_keyword:
            return []
        current_year = self._today().year
        filings: dict[str, Filing] = {}
        for year in range(current_year, current_year - max(1, years), -1):
            for row in await self._ipe_rows(year):
                haystack = self._normalize_search(
                    " ".join(
                        row.get(field, "") for field in ("Assunto", "Tipo", "Categoria")
                    )
                )
                if clean_keyword not in haystack:
                    continue
                filing = self._filing_from_row(row, category=None)
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

    async def list_major_holders(
        self,
        company_id_or_code: str,
        *,
        limit: int = 25,
    ) -> list[MajorHolderNotification]:
        """Return shareholder positions from the issuer's structured FRE forms."""

        code = self.normalize_cvm_code(company_id_or_code)
        companies = await self._company_registry()
        company = next(
            (candidate for candidate in companies if candidate.local_code == code),
            None,
        )
        if company is None:
            raise SourceError(
                f"CVM code {code} is not an active Brazilian exchange-listed issuer."
            )

        holders: dict[tuple[str, str], MajorHolderNotification] = {}
        current_year = self._today().year
        for year in range(current_year, current_year - self._history_years, -1):
            for row in await self._fre_position_rows(year):
                if self._digits(row.get("CNPJ_Companhia", "")) != self._digits(
                    company.source_id
                ):
                    continue
                holder = self._major_holder_from_row(row, company=company, year=year)
                if holder is None:
                    continue
                holder_key = self._digits(row.get("CPF_CNPJ_Acionista", ""))
                holders[(holder.filing_id, holder_key or holder.holder_name)] = holder
            if len(holders) >= max(1, limit):
                break
        return self._sort_major_holders(holders.values())[: max(1, limit)]

    async def search_major_holders(
        self,
        holder_name: str,
        *,
        limit: int = 25,
    ) -> list[MajorHolderNotification]:
        """Search structured FRE shareholder positions across Brazilian issuers."""

        clean_name = self._normalize_search(holder_name)
        if not clean_name:
            return []
        companies = await self._company_registry()
        companies_by_cnpj = {
            self._digits(company.source_id): company for company in companies
        }
        matches: dict[tuple[str, str], MajorHolderNotification] = {}
        current_year = self._today().year
        for year in range(current_year, current_year - self._history_years, -1):
            for row in await self._fre_position_rows(year):
                if clean_name not in self._normalize_search(row.get("Acionista", "")):
                    continue
                company = companies_by_cnpj.get(
                    self._digits(row.get("CNPJ_Companhia", ""))
                )
                if company is None:
                    continue
                holder = self._major_holder_from_row(row, company=company, year=year)
                if holder is None:
                    continue
                holder_key = self._digits(row.get("CPF_CNPJ_Acionista", ""))
                matches[(holder.filing_id, holder_key or holder.holder_name)] = holder
            if len(matches) >= max(1, limit):
                break
        return self._sort_major_holders(matches.values())[: max(1, limit)]

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

    async def structured_archive(self, dataset: str, year: int) -> bytes | None:
        """Fetch CVM's Open Data DFP/ITR bulk dataset ZIP for one year.

        Returns None if that year has no published archive (e.g. before
        2010, or the current year before CVM has published it) rather than
        raising, so callers can fall back to the PDF filing.
        """

        key = (dataset, year)
        if key in self._structured_archives:
            return self._structured_archives[key]
        response = await self._request(
            "GET",
            self.structured_archive_url(dataset, year),
            allow_not_found=True,
        )
        if response.status_code == 404:
            self._structured_archives[key] = None
            return None
        if len(response.content) > _MAX_STRUCTURED_ARCHIVE_BYTES:
            raise SourceError(
                f"The CVM {dataset.upper()} archive for {year} is unexpectedly large."
            )
        self._structured_archives[key] = response.content
        return response.content

    @staticmethod
    def structured_archive_url(dataset: str, year: int) -> str:
        return STRUCTURED_ARCHIVE_URL.format(
            dataset=dataset, dataset_upper=dataset.upper(), year=year
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

    async def _vlmo_rows(self, year: int) -> list[dict[str, str]]:
        """Per-issuer "Valores Mobiliarios Negociados e Detidos" (insider
        trading and holdings, CVM Instrucao 358 art. 11) filing index for
        one year - same row shape as the IPE archive, fetched from its own
        yearly Open Data archive instead."""

        archive = await self.structured_archive("vlmo", year)
        if archive is None:
            return []
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as zip_archive:
                members = [
                    member
                    for member in zip_archive.infolist()
                    if not member.is_dir()
                    and member.filename.casefold().endswith(".csv")
                    and "_con_" not in member.filename.casefold()
                ]
                if len(members) != 1:
                    raise SourceError(
                        f"The CVM VLMO archive for {year} has unexpected contents."
                    )
                member = members[0]
                if member.file_size > _MAX_ARCHIVE_EXPANDED_BYTES:
                    raise SourceError(
                        f"The CVM VLMO archive for {year} expands beyond the safe "
                        "limit."
                    )
                return self._csv_rows(zip_archive.read(member))
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise SourceError(f"The CVM VLMO archive for {year} is invalid.") from exc

    async def _fre_position_rows(self, year: int) -> list[dict[str, str]]:
        """Shareholder-position section of the yearly structured FRE archive."""

        archive = await self.structured_archive("fre", year)
        if archive is None:
            return []
        expected_name = f"fre_cia_aberta_posicao_acionaria_{year}.csv"
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as zip_archive:
                members = [
                    member
                    for member in zip_archive.infolist()
                    if not member.is_dir()
                    and member.filename.rsplit("/", 1)[-1].casefold() == expected_name
                ]
                if len(members) != 1:
                    raise SourceError(
                        f"The CVM FRE archive for {year} does not contain one "
                        "shareholder-position table."
                    )
                member = members[0]
                if member.file_size > _MAX_ARCHIVE_EXPANDED_BYTES:
                    raise SourceError(
                        f"The CVM FRE shareholder table for {year} expands beyond "
                        "the safe limit."
                    )
                return self._csv_rows(zip_archive.read(member))
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise SourceError(f"The CVM FRE archive for {year} is invalid.") from exc

    def _major_holder_from_row(
        self,
        row: dict[str, str],
        *,
        company: Company,
        year: int,
    ) -> MajorHolderNotification | None:
        document_id = row.get("ID_Documento", "").strip()
        holder_name = row.get("Acionista", "").strip()
        if not document_id.isdigit() or not holder_name:
            return None
        return MajorHolderNotification(
            filing_id=f"br_cvm_fre_{document_id}",
            company_id=company.id,
            issuer_name=row.get("Nome_Companhia", "").strip() or company.name,
            holder_name=holder_name,
            position_date=self._parse_date(row.get("Data_Referencia")),
            total_percent=self._parse_decimal(
                row.get("Percentual_Total_Acoes_Circulacao")
            ),
            total_voting_rights=self._parse_integer(
                row.get("Quantidade_Total_Acoes_Circulacao")
            ),
            source_url=self.structured_archive_url("fre", year),
        )

    @staticmethod
    def _sort_major_holders(
        holders: Iterable[MajorHolderNotification],
    ) -> list[MajorHolderNotification]:
        return sorted(
            holders,
            key=lambda holder: (
                holder.position_date or date.min,
                holder.filing_id,
                holder.holder_name,
            ),
            reverse=True,
        )

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
        is_insider = category is not None and category.casefold() == "insider"
        if category and category.casefold() == "accounts" and filing_type is None:
            return None
        if (
            category
            and category.casefold() != "accounts"
            and not is_insider
            and self._normalize_search(category)
            != self._normalize_search(source_category)
        ):
            return None
        if filing_type is None:
            filing_type = (
                "insider"
                if is_insider
                else self._slug(source_type or source_category or "document")
            )

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
            category=(
                "accounts"
                if normalized_type in _FINANCIAL_TYPES
                else "insider"
                if is_insider
                else self._slug(source_category)
            ),
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
    def _digits(value: str) -> str:
        return "".join(character for character in value if character.isdigit())

    @staticmethod
    def _parse_decimal(value: str | None) -> Decimal | None:
        clean_value = (value or "").strip().replace(",", ".")
        if not clean_value:
            return None
        try:
            return Decimal(clean_value)
        except InvalidOperation:
            return None

    @staticmethod
    def _parse_integer(value: str | None) -> int | None:
        clean_value = (value or "").strip().replace(".", "").replace(",", "")
        try:
            return int(clean_value) if clean_value else None
        except ValueError:
            return None

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
