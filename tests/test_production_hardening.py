from __future__ import annotations

from pathlib import Path

import pytest

from openfilings.smoke import SMOKE_CASES, run_live_smoke

ROOT = Path(__file__).resolve().parents[1]


def test_keyless_smoke_matrix_covers_every_enabled_source() -> None:
    assert {case.source for case in SMOKE_CASES} == {
        "fca_nsm",
        "esef",
        "cvm",
        "sgx",
        "bmv",
        "nse",
        "smv",
        "sfc",
        "sedar",
        "edinet",
        "kap",
    }
    assert all(case.query and case.label for case in SMOKE_CASES)
    # SEDAR+ and EDINET have no keyless filing-search API (SEDAR+ blocks
    # automated queries; EDINET's filing API needs a paid-free key), so
    # only company discovery is checked for them.
    assert {case.source for case in SMOKE_CASES if not case.check_financials} == {
        "sedar",
        "edinet",
    }


@pytest.mark.asyncio
async def test_live_smoke_runner_checks_company_and_filing_resolution() -> None:
    service = _FakeService()

    results = await run_live_smoke(service, cases=SMOKE_CASES[:2], timeout_seconds=1)

    assert len(results) == 2
    assert results[0].company_id == "company-fca_nsm"
    assert results[0].filing_id == "filing-fca_nsm"
    assert results[0].identity_check == "not_applicable (no balance sheet extracted)"
    assert service.sources == ["fca_nsm", "esef"]


def test_ci_workflows_enforce_tests_security_and_keyless_live_checks() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    security = (ROOT / ".github/workflows/security.yml").read_text(encoding="utf-8")
    live = (ROOT / ".github/workflows/live-smoke.yml").read_text(encoding="utf-8")

    assert "uv sync --locked --all-extras --dev" in ci
    assert "uv run ruff check ." in ci
    assert "uv run ruff format --check ." in ci
    assert "uv run pytest" in ci
    assert "uv build" in ci
    assert "codeql-action/analyze@v4" in security
    assert "pip-audit" in security
    assert "openfilings.smoke" in live
    assert "EDINET_API_KEY" not in live


class _FakeFinancials:
    @staticmethod
    def balance_sheet() -> None:
        return None


class _FakeFiling:
    def __init__(self, source: str) -> None:
        self.id = f"filing-{source}"

    async def financials(self, **_: object) -> _FakeFinancials:
        return _FakeFinancials()


class _FakeFilings:
    def __init__(self, source: str) -> None:
        self._filing = _FakeFiling(source)

    def latest(self) -> _FakeFiling:
        return self._filing

    def __getitem__(self, index: slice) -> list[_FakeFiling]:
        return [self._filing][index]


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
