"""Canadian TSX discovery with SEDAR+ filing provenance."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

import httpx

from openfilings._version import __version__
from openfilings.adapters._common import RetryingClient
from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.models import Company, Filing, SourceName

TSX_SEARCH_URL = "https://www.tsx.com/json/company-directory/search/{exchange}/{query}"
TSX_DIRECTORY_URL = (
    "https://www.tsx.com/en/listings/listing-with-us/listed-company-directory"
)
SEDAR_SEARCH_URL = (
    "https://www.sedarplus.ca/csa-security/relay.html?target=csa-party&"
    "targetAppCode=csa-security&url=https%3A%2F%2Fwww.sedarplus.ca%2F"
    "csa-party%2Fservice%2Fcreate.html%3FtargetAppCode%3Dcsa-security%26"
    "service%3DsearchDocuments"
)

_NON_COMPANY_MARKERS = (
    " etf",
    " fund",
    " shares etf",
    " income trust",
    " split corp",
)
MAX_SEDAR_DOCUMENT_BYTES = 100 * 1024 * 1024
_MAX_REDIRECTS = 5


class SedarClient(RetryingClient):
    """Discover TSX/TSXV companies and direct users to public SEDAR+ records."""

    source: SourceName = "sedar"

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            "TSX/SEDAR+",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            headers={
                "User-Agent": f"openfilings/{__version__}",
                "Referer": TSX_DIRECTORY_URL,
            },
            client=client,
        )

    async def search_companies(self, query: str, *, limit: int = 10) -> list[Company]:
        clean_query = query.strip()
        if clean_query.casefold().startswith("ca_sedar_"):
            clean_query = clean_query[len("ca_sedar_") :]
        if not clean_query:
            return []
        companies: dict[str, Company] = {}
        for exchange in ("tsx", "tsxv"):
            response = await self._request(
                "GET",
                TSX_SEARCH_URL.format(exchange=exchange, query=clean_query),
            )
            try:
                rows = response.json().get("results", [])
            except (ValueError, AttributeError) as exc:
                raise SourceError("TSX returned an invalid company response.") from exc
            for row in rows if isinstance(rows, list) else []:
                company = self._company_from_row(exchange, row)
                if company is not None:
                    companies.setdefault(company.id, company)
        ordered = sorted(
            companies.values(),
            key=lambda company: (
                0 if company.ticker == clean_query.upper() else 1,
                company.name,
            ),
        )
        return ordered[: max(1, limit)]

    async def list_filings(
        self,
        company_id: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
    ) -> list[Filing]:
        self._company_key(company_id)
        raise SourceError(
            "SEDAR+ permits public browser document search but currently blocks "
            "non-browser automated filing queries. Open the company's public "
            f"document search at {SEDAR_SEARCH_URL} until CSA provides a stable feed."
        )

    async def download_document(self, document_id: str) -> SourceDocument:
        url = validate_sedar_document_url(document_id)
        for _ in range(_MAX_REDIRECTS + 1):
            response = await self._request(
                "GET",
                url,
                follow_redirects=False,
                _return_redirects=True,
            )
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise DocumentUnavailableError(
                        "SEDAR+ returned a redirect without a destination."
                    )
                url = validate_sedar_document_url(urljoin(url, location))
                continue
            return _pdf_document(response, source_url=url)
        raise DocumentUnavailableError("SEDAR+ returned too many document redirects.")

    def matches_company_id(self, value: str) -> bool:
        return value.casefold().startswith("ca_sedar_")

    def matches_filing_id(self, value: str) -> bool:
        return value.casefold().startswith("ca_sedar_filing_")

    @staticmethod
    def _company_from_row(exchange: str, row: object) -> Company | None:
        if not isinstance(row, dict):
            return None
        symbol = str(row.get("symbol", "")).strip().upper()
        name = str(row.get("name", "")).strip()
        lowered_name = name.casefold()
        if (
            not re.fullmatch(r"[A-Z0-9.-]{1,20}", symbol)
            or not name
            or any(marker in lowered_name for marker in _NON_COMPANY_MARKERS)
        ):
            return None
        return Company(
            id=f"ca_sedar_{exchange}_{symbol}",
            source_id=f"{exchange}:{symbol}",
            name=name,
            sources=("sedar",),
            market="CA",
            country_code="CA",
            ticker=symbol,
            local_code=symbol,
            status="active exchange-listed issuer",
            company_type=exchange.upper(),
            source_url=SEDAR_SEARCH_URL,
        )

    @staticmethod
    def _company_key(value: str) -> tuple[str, str]:
        match = re.fullmatch(r"ca_sedar_(tsx|tsxv)_([A-Z0-9.-]{1,20})", value, re.I)
        if match is None:
            raise SourceError(
                "Expected a Canadian company ID shaped like ca_sedar_tsx_SHOP."
            )
        return match.group(1).casefold(), match.group(2).upper()


def validate_sedar_document_url(value: str) -> str:
    """Return a normalized HTTPS URL confined to the official SEDAR+ host."""

    url = value.strip()
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if (
        parsed.scheme.casefold() != "https"
        or hostname not in {"sedarplus.ca", "www.sedarplus.ca"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not parsed.path.startswith(("/csa-party/", "/csa-security/"))
    ):
        raise DocumentUnavailableError(
            "Expected an HTTPS SEDAR+ generated document URL on "
            "www.sedarplus.ca/csa-party/."
        )
    return parsed.geturl()


def validate_sedar_pdf(
    data: bytes,
    *,
    source_url: str,
) -> SourceDocument:
    """Validate a bounded user-supplied SEDAR+ PDF."""

    if not data:
        raise DocumentUnavailableError("The SEDAR+ document is empty.")
    if len(data) > MAX_SEDAR_DOCUMENT_BYTES:
        raise DocumentUnavailableError("The SEDAR+ document exceeds the 100 MB limit.")
    if data[:4] != b"%PDF":
        if _looks_like_html(data):
            raise DocumentUnavailableError(
                "SEDAR+ returned a browser verification or search page, not a PDF. "
                "Download the document in your browser and import the local PDF."
            )
        raise DocumentUnavailableError("The supplied SEDAR+ document is not a PDF.")
    return SourceDocument(
        data=data,
        media_type="application/pdf",
        source_url=source_url,
        profile="sedar-import",
    )


def _pdf_document(response: httpx.Response, *, source_url: str) -> SourceDocument:
    content_length = response.headers.get("content-length")
    try:
        declared_bytes = int(content_length) if content_length else None
    except ValueError:
        declared_bytes = None
    if declared_bytes is not None and declared_bytes > MAX_SEDAR_DOCUMENT_BYTES:
        raise DocumentUnavailableError("The SEDAR+ document exceeds the 100 MB limit.")
    return validate_sedar_pdf(
        response.content,
        source_url=source_url,
    )


def _looks_like_html(data: bytes) -> bool:
    prefix = data[:1024].lstrip().lower()
    return (
        prefix.startswith((b"<!doctype html", b"<html", b"<?xml")) or b"<html" in prefix
    )
