from __future__ import annotations

import gzip

import httpx
import pytest

from openfilings.adapters._common import bounded_request
from openfilings.exceptions import SourceError


@pytest.mark.asyncio
async def test_bounded_request_rejects_oversized_declared_response() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-length": "9"},
            content=b"123456789",
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SourceError, match="8 byte limit"):
            await bounded_request(
                client, "GET", "https://example.test/report", max_bytes=8
            )


@pytest.mark.asyncio
async def test_bounded_request_keeps_response_content_for_callers() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"small response")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await bounded_request(
            client, "GET", "https://example.test/report", max_bytes=20
        )

    assert response.content == b"small response"


@pytest.mark.asyncio
async def test_bounded_request_preserves_http_content_decoding() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            content=gzip.compress(b'{"result": "decoded"}'),
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await bounded_request(
            client, "GET", "https://example.test/api", max_bytes=100
        )

    assert response.json() == {"result": "decoded"}
