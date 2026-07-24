from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from openfilings.adapters.fca_nsm import FcaNsmClient
from openfilings.exceptions import DocumentUnavailableError


@pytest.mark.asyncio
async def test_search_and_list_normalize_nsm_issuers_and_filings() -> None:
    request_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.params["index"] == "nsm-search"
            payload = json.loads(request.content)
            request_payloads.append(payload)
            return httpx.Response(200, json=_search_response())
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = FcaNsmClient(client=http_client)
        companies = await source.search_issuers("Tesco", limit=10)
        filings = await source.list_filings("uk_lei_2138002P5RNKC5W2JZ46")

    company = next(item for item in companies if item.name == "TESCO PLC")
    assert company.id == "uk_lei_2138002P5RNKC5W2JZ46"
    assert company.sources == ("fca_nsm",)
    assert filings[0].id == "uk_nsm_5ebfff2c-6970-4b9a-8357-53e04bcf1ed5"
    assert filings[0].source == "fca_nsm"
    assert filings[0].media_type == "text/html"
    assert filings[0].related_issuers[0].name == "TESCO PLC"

    criteria_object = cast(dict[str, Any], request_payloads[0]["criteriaObj"])
    first_criterion = criteria_object["criteria"][0]
    assert first_criterion["name"] == "company_lei"
    assert first_criterion["value"] == [
        "Tesco",
        "",
        "disclose_org",
        "related_org",
    ]


@pytest.mark.asyncio
async def test_download_returns_media_type_and_provenance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/artefacts/NSM/RNS/disclosure-123.html"
        return httpx.Response(
            200,
            headers={"content-type": "text/html;charset=UTF-8"},
            content=b"<html><body>Results</body></html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = FcaNsmClient(client=http_client)
        document = await source.download_document("NSM/RNS/disclosure-123.html")

    assert document.media_type == "text/html"
    assert document.data.startswith(b"<html>")
    assert document.source_url.endswith("/NSM/RNS/disclosure-123.html")


@pytest.mark.asyncio
async def test_search_disclosures_uses_headline_criterion_not_top_level_keyword() -> (
    None
):
    request_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        request_payloads.append(payload)
        return httpx.Response(200, json=_search_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = FcaNsmClient(client=http_client)
        filings = await source.search_disclosures("Tesco", type_codes=["HOL"], limit=5)

    assert filings
    payload = request_payloads[0]
    assert payload["keyword"] is None
    criteria = cast(list[dict[str, Any]], payload["criteriaObj"]["criteria"])
    assert {"name": "headline", "value": "Tesco"} in criteria
    assert {"name": "type_code", "value": ["hol"]} in criteria


@pytest.mark.asyncio
async def test_search_disclosures_without_keyword_browses_by_type_code() -> None:
    request_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_payloads.append(json.loads(request.content))
        return httpx.Response(200, json=_search_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        source = FcaNsmClient(client=http_client)
        filings = await source.search_disclosures(None, type_codes=["HOL"], limit=5)

    assert filings
    criteria = cast(
        list[dict[str, Any]], request_payloads[0]["criteriaObj"]["criteria"]
    )
    assert not any(item["name"] == "headline" for item in criteria)


@pytest.mark.asyncio
async def test_search_disclosures_empty_keyword_short_circuits() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(AssertionError("should not request"))
        )
    ) as http_client:
        source = FcaNsmClient(client=http_client)
        filings = await source.search_disclosures("   ")

    assert filings == []


def test_document_url_rejects_paths_outside_nsm_artefacts() -> None:
    with pytest.raises(DocumentUnavailableError, match="Unsafe"):
        FcaNsmClient.document_url("https://example.test/document.pdf")


def _search_response() -> dict[str, object]:
    fixture = Path(__file__).parent / "fixtures" / "fca_nsm_tesco_search.json"
    return json.loads(fixture.read_text(encoding="utf-8"))
