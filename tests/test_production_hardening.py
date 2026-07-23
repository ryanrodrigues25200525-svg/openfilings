from __future__ import annotations

from pathlib import Path

import pytest

from openfilings.smoke import SMOKE_CASES, run_live_smoke

ROOT = Path(__file__).resolve().parents[1]


def test_keyless_smoke_matrix_covers_every_enabled_source_except_edinet() -> None:
    assert {case.source for case in SMOKE_CASES} == {
        "fca_nsm",
        "esef",
        "cvm",
        "sgx",
        "bmv",
        "nse",
        "smv",
        "sfc",
    }
    assert all(case.query and case.label for case in SMOKE_CASES)


@pytest.mark.asyncio
async def test_live_smoke_runner_checks_company_and_filing_resolution() -> None:
    service = _FakeService()

    results = await run_live_smoke(service, cases=SMOKE_CASES[:2], timeout_seconds=1)

    assert len(results) == 2
    assert results[0].company_id == "company-fca_nsm"
    assert results[0].filing_id == "filing-fca_nsm"
    assert service.sources == ["fca_nsm", "esef"]


def test_ci_workflows_enforce_tests_security_and_keyless_live_checks() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    security = (ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8")
    live = (ROOT / ".github/workflows/live-smoke.yml").read_text(encoding="utf-8")

    assert "uv sync --locked --all-extras --dev" in ci
    assert "uv run ruff check src tests" in ci
    assert "uv run pytest" in ci
    assert "uv build" in ci
    assert "codeql-action/analyze@v4" in security
    assert "pip-audit" in security
    assert "openfilings.smoke" in live
    assert "EDINET_API_KEY" not in live


class _FakeFiling:
    def __init__(self, source: str) -> None:
        self.id = f"filing-{source}"


class _FakeFilings:
    def __init__(self, source: str) -> None:
        self._filing = _FakeFiling(source)

    def latest(self) -> _FakeFiling:
        return self._filing


class _FakeCompany:
    def __init__(self, source: str) -> None:
        self.id = f"company-{source}"
        self._source = source

    async def get_filings(self, **_: object) -> _FakeFilings:
        return _FakeFilings(self._source)


class _FakeService:
    def __init__(self) -> None:
        self.sources: list[str] = []

    async def company(self, _: str, *, source: str) -> _FakeCompany:
        self.sources.append(source)
        return _FakeCompany(source)
