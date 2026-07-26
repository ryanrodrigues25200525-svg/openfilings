"""Small, dependency-free helpers shared by public-market adapters."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Any

import httpx

from openfilings.exceptions import SourceError
from openfilings.limits import MAX_TAGGED_DOCUMENT_BYTES


async def bounded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_bytes: int = MAX_TAGGED_DOCUMENT_BYTES,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request without allowing HTTPX to buffer an unbounded body."""

    follow_redirects = kwargs.pop("follow_redirects", None)
    request = client.build_request(method, url, **kwargs)
    send_options = (
        {"follow_redirects": follow_redirects} if follow_redirects is not None else {}
    )
    response = await client.send(
        request,
        stream=True,
        **send_options,
    )
    declared = response.headers.get("content-length")
    try:
        if declared is not None and int(declared) > max_bytes:
            await response.aclose()
            raise SourceError(f"Response exceeds the {max_bytes} byte limit.")
    except ValueError:
        pass
    # Mock transports may provide an already-materialized response. Production
    # requests reach this method with an unconsumed stream.
    if response.is_stream_consumed:
        if len(response.content) > max_bytes:
            raise SourceError(f"Response exceeds the {max_bytes} byte limit.")
        return response
    data = bytearray()
    try:
        # ``aiter_bytes`` retains HTTPX's normal content decoding. Using raw
        # chunks here would leave gzip-compressed bytes in ``response.content``
        # after we materialize the bounded stream, breaking JSON and XML
        # consumers for regulators that compress API replies.
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > max_bytes:
                raise SourceError(f"Response exceeds the {max_bytes} byte limit.")
    except Exception:
        await response.aclose()
        raise
    response._content = bytes(data)  # HTTPX has consumed the stream above.
    return response


class RetryingClient:
    """Bounded HTTP wrapper with consistent regulator error handling."""

    def __init__(
        self,
        source_label: str,
        *,
        timeout_seconds: float,
        max_retries: int,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._source_label = source_label
        self._max_retries = max_retries
        self._owns_client = client is None
        self._default_headers = headers or {}
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds),
            headers=headers,
        )

    async def __aenter__(self) -> RetryingClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        return_redirects = bool(kwargs.pop("_return_redirects", False))
        request_headers = {
            **self._default_headers,
            **dict(kwargs.pop("headers", {}) or {}),
        }
        kwargs["headers"] = request_headers
        for attempt in range(self._max_retries + 1):
            try:
                response = await bounded_request(self._client, method, url, **kwargs)
            except httpx.RequestError as exc:
                if attempt >= self._max_retries:
                    detail = str(exc).strip() or type(exc).__name__
                    raise SourceError(
                        f"{self._source_label} request failed: {detail}"
                    ) from exc
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            if response.status_code not in {429, 500, 502, 503, 504}:
                if return_redirects and response.is_redirect:
                    return response
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    detail = response.text.strip()[:300] or response.reason_phrase
                    message = (
                        f"{self._source_label} returned "
                        f"{response.status_code}: {detail}"
                    )
                    raise SourceError(message) from exc
                return response
            if attempt >= self._max_retries:
                detail = response.text.strip()[:300] or response.reason_phrase
                raise SourceError(
                    f"{self._source_label} returned {response.status_code}: {detail}"
                )
            retry_after = response.headers.get("retry-after")
            try:
                delay = (
                    min(float(retry_after), 30.0) if retry_after else 0.5 * 2**attempt
                )
            except ValueError:
                delay = 0.5 * 2**attempt
            await asyncio.sleep(delay)
        raise AssertionError("retry loop exited unexpectedly")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(re.sub(r"[^\w]+", " ", plain).split())


def slug(value: str) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", normalize_text(value))) or "document"


def utc_midnight(value: date) -> datetime:
    return datetime.combine(value, datetime.min.time(), tzinfo=UTC)


def ranked_matches(
    query: str,
    records: list[tuple[tuple[str, ...], Any]],
    *,
    limit: int,
) -> list[Any]:
    normalized_query = normalize_text(query)
    if not normalized_query:
        return []
    ranked: list[tuple[int, str, Any]] = []
    for fields, record in records:
        normalized_fields = tuple(normalize_text(field) for field in fields)
        if not any(normalized_query in field for field in normalized_fields):
            continue
        if normalized_query in normalized_fields:
            rank = 0
        elif any(field.startswith(normalized_query) for field in normalized_fields):
            rank = 1
        else:
            rank = 2
        ranked.append((rank, normalized_fields[-1], record))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [record for _, _, record in ranked[: max(1, limit)]]
