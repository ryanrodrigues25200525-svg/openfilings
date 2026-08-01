"""Retry backoff and jitter behavior shared by every adapter."""

from __future__ import annotations

import httpx
import pytest

from openfilings.adapters._common import RetryingClient, _jittered
from openfilings.exceptions import SourceError


def test_jitter_stays_within_proportional_bounds() -> None:
    for _ in range(200):
        delayed = _jittered(1.0)
        assert 0.75 <= delayed <= 1.25


def test_jitter_is_not_constant() -> None:
    """A fixed schedule retries every failing adapter in lockstep; confirm
    the jitter actually varies rather than being a no-op wrapper."""

    values = {_jittered(1.0) for _ in range(20)}
    assert len(values) > 1


@pytest.mark.asyncio
async def test_retry_after_header_is_respected_without_jitter(monkeypatch) -> None:
    """A server's own Retry-After is an explicit instruction, not a schedule
    to jitter - jittering it would defeat the point of respecting it."""

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "7"})
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = RetryingClient("Test", timeout_seconds=5, max_retries=2, client=http)
        response = await client._request("GET", "https://example.test/x")

    assert response.status_code == 200
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_exhausted_retries_raise_source_error(monkeypatch) -> None:
    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = RetryingClient("Test", timeout_seconds=5, max_retries=2, client=http)
        with pytest.raises(SourceError, match="503"):
            await client._request("GET", "https://example.test/x")
