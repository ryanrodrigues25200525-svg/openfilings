"""Async client and normalizer for Companies House public APIs."""

from __future__ import annotations

import asyncio
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from openfilings.adapters.base import SourceDocument
from openfilings.exceptions import DocumentUnavailableError, SourceError
from openfilings.models import Company, Filing

PUBLIC_DATA_BASE_URL = "https://api.company-information.service.gov.uk"
DOCUMENT_BASE_URL = "https://document-api.company-information.service.gov.uk"
COMPANY_WEB_BASE_URL = "https://find-and-update.company-information.service.gov.uk"
_DOCUMENT_PREFERENCES = (
    "application/xhtml+xml",
    "application/zip",
    "application/pdf",
)
_MAX_DOCUMENT_BYTES = 100 * 1024 * 1024


class CompaniesHouseClient:
    """Small Companies House client with normalization and bounded retries."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            auth=httpx.BasicAuth(api_key, ""),
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": "openfilings/0.1"},
        )

    async def __aenter__(self) -> CompaniesHouseClient:
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

        response = await self._request(
            "GET",
            f"{PUBLIC_DATA_BASE_URL}/search/companies",
            params={"q": clean_query, "items_per_page": min(max(limit, 1), 100)},
        )
        items = response.json().get("items", [])
        return [self._company_from_item(item) for item in items[:limit]]

    async def list_filings(
        self,
        company_number: str,
        *,
        category: str | None = "accounts",
        limit: int = 25,
    ) -> list[Filing]:
        company_number = self.normalize_company_number(company_number)
        wanted = min(max(limit, 1), 500)
        filings: list[Filing] = []
        start_index = 0

        while len(filings) < wanted:
            page_size = min(100, wanted - len(filings))
            params: dict[str, str | int] = {
                "items_per_page": page_size,
                "start_index": start_index,
            }
            if category:
                params["category"] = category

            response = await self._request(
                "GET",
                f"{PUBLIC_DATA_BASE_URL}/company/{company_number}/filing-history",
                params=params,
            )
            payload = response.json()
            items = payload.get("items", [])
            filings.extend(
                self._filing_from_item(company_number, item) for item in items
            )

            start_index += len(items)
            if not items or start_index >= payload.get("total_count", start_index):
                break

        return filings[:wanted]

    async def get_filing(self, company_number: str, transaction_id: str) -> Filing:
        company_number = self.normalize_company_number(company_number)
        response = await self._request(
            "GET",
            (
                f"{PUBLIC_DATA_BASE_URL}/company/{company_number}"
                f"/filing-history/{transaction_id}"
            ),
        )
        return self._filing_from_item(company_number, response.json())

    async def download_pdf(self, document_id: str) -> bytes:
        response = await self._request(
            "GET",
            f"{DOCUMENT_BASE_URL}/document/{document_id}/content",
            headers={"Accept": "application/pdf"},
        )
        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        if media_type and media_type != "application/pdf":
            raise DocumentUnavailableError(
                f"Document {document_id} was returned as {media_type}, "
                "not application/pdf."
            )
        if not response.content.startswith(b"%PDF"):
            raise DocumentUnavailableError(
                f"Document {document_id} did not contain a valid PDF signature."
            )
        return response.content

    async def download_document(self, document_id: str) -> SourceDocument:
        """Prefer tagged accounts before falling back to a rendered PDF."""

        metadata = await self.document_metadata(document_id)
        resources = metadata.get("resources", {})
        media_type = next(
            (
                candidate
                for candidate in _DOCUMENT_PREFERENCES
                if candidate in resources
            ),
            None,
        )
        if media_type is None:
            available = ", ".join(sorted(resources)) or "none"
            raise DocumentUnavailableError(
                f"Document {document_id} has no supported resource; available: "
                f"{available}."
            )
        response = await self._request(
            "GET",
            f"{DOCUMENT_BASE_URL}/document/{document_id}/content",
            headers={"Accept": media_type},
        )
        if not response.content:
            raise DocumentUnavailableError(f"Document {document_id} was empty.")
        if len(response.content) > _MAX_DOCUMENT_BYTES:
            raise DocumentUnavailableError(
                f"Document {document_id} exceeds the "
                f"{_MAX_DOCUMENT_BYTES // 1024 // 1024} MB limit."
            )
        returned_type = response.headers.get("content-type", "").split(";", 1)[0]
        if returned_type and returned_type != "application/octet-stream":
            media_type = returned_type
        return SourceDocument(
            data=response.content,
            media_type=media_type,
            source_url=f"{DOCUMENT_BASE_URL}/document/{document_id}/content",
        )

    async def document_metadata(self, document_id: str) -> dict[str, Any]:
        """Return document metadata including every available representation."""

        response = await self._request(
            "GET", f"{DOCUMENT_BASE_URL}/document/{document_id}"
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError(
                f"Companies House document metadata {document_id} was not JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise SourceError(
                f"Companies House document metadata {document_id} was malformed."
            )
        return payload

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                if attempt >= self._max_retries:
                    raise SourceError(f"Companies House request failed: {exc}") from exc
                await asyncio.sleep(0.25 * (2**attempt))
                continue

            if response.status_code not in {429, 500, 502, 503, 504}:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    detail = self._error_detail(response)
                    raise SourceError(
                        f"Companies House returned {response.status_code}: {detail}"
                    ) from exc
                return response

            if attempt >= self._max_retries:
                detail = self._error_detail(response)
                raise SourceError(
                    f"Companies House returned {response.status_code}: {detail}"
                )
            await asyncio.sleep(self._retry_delay(response, attempt))

        raise AssertionError("retry loop exited unexpectedly")

    @staticmethod
    def normalize_company_number(company_id_or_number: str) -> str:
        value = company_id_or_number.strip()
        return value[3:] if value.lower().startswith("uk_") else value

    @classmethod
    def _company_from_item(cls, item: dict[str, Any]) -> Company:
        company_number = str(item["company_number"])
        address = item.get("address_snippet") or cls._format_address(
            item.get("address")
        )
        return Company(
            id=f"uk_{company_number}",
            source_id=company_number,
            name=str(item["title"]),
            status=item.get("company_status"),
            company_type=item.get("company_type"),
            address=address,
            source_url=f"{COMPANY_WEB_BASE_URL}/company/{company_number}",
        )

    @classmethod
    def _filing_from_item(cls, company_number: str, item: dict[str, Any]) -> Filing:
        transaction_id = str(item["transaction_id"])
        metadata_url = item.get("links", {}).get("document_metadata")
        document_id = cls._document_id_from_url(metadata_url)
        description = item.get("description")
        return Filing(
            id=f"uk_{company_number}_{transaction_id}",
            company_id=f"uk_{company_number}",
            source_id=transaction_id,
            title=cls._filing_title(item),
            category=str(item.get("category", "unknown")),
            filing_type=str(item.get("type", "unknown")),
            filing_date=date.fromisoformat(str(item["date"])),
            description=description,
            pages=item.get("pages"),
            document_id=document_id,
            document_metadata_url=metadata_url,
            media_type=None,
            source_url=(
                f"{COMPANY_WEB_BASE_URL}/company/{company_number}"
                f"/filing-history/{transaction_id}"
            ),
        )

    @staticmethod
    def _filing_title(item: dict[str, Any]) -> str:
        description = str(item.get("description", "Filing")).replace("-", " ")
        description = " ".join(description.split()).capitalize()
        return f"{description} ({item.get('type', 'unknown')})"

    @staticmethod
    def _document_id_from_url(url: str | None) -> str | None:
        if not url:
            return None
        path = urlparse(url).path.rstrip("/")
        return path.rsplit("/", 1)[-1] or None

    @staticmethod
    def _format_address(address: dict[str, Any] | None) -> str | None:
        if not address:
            return None
        ordered_keys = (
            "premises",
            "address_line_1",
            "address_line_2",
            "locality",
            "region",
            "postal_code",
            "country",
        )
        parts = [str(address[key]) for key in ordered_keys if address.get(key)]
        return ", ".join(parts) or None

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
            return str(payload.get("error") or payload.get("message") or payload)
        except ValueError:
            return response.text[:300] or response.reason_phrase

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                try:
                    delay = (
                        parsedate_to_datetime(retry_after).timestamp()
                        - parsedate_to_datetime(response.headers["date"]).timestamp()
                    )
                    return min(max(delay, 0.0), 30.0)
                except (KeyError, TypeError, ValueError):
                    pass
        return 0.25 * (2**attempt)
