"""Bounded live checks for keyless regulator integrations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from openfilings.service import OpenFilingsService


@dataclass(frozen=True, slots=True)
class SmokeCase:
    label: str
    query: str
    source: str


@dataclass(frozen=True, slots=True)
class SmokeResult:
    label: str
    company_id: str
    filing_id: str


SMOKE_CASES = (
    SmokeCase("UK FCA NSM", "Tesco", "fca_nsm"),
    SmokeCase("European ESEF", "ASML", "esef"),
    SmokeCase("Brazil CVM", "Banco do Brasil", "cvm"),
    SmokeCase("Singapore SGX", "S68", "sgx"),
    SmokeCase("Mexico BMV", "AMX", "bmv"),
    SmokeCase("India NSE", "RELIANCE", "nse"),
    SmokeCase("Peru SMV", "Alicorp", "smv"),
    SmokeCase("Colombia SFC", "Ecopetrol", "sfc"),
)


async def run_live_smoke(
    service: Any,
    *,
    cases: tuple[SmokeCase, ...] = SMOKE_CASES,
    timeout_seconds: float = 90.0,
) -> tuple[SmokeResult, ...]:
    """Check one company and current filing per keyless source family."""

    results: list[SmokeResult] = []
    failures: list[str] = []
    for case in cases:
        try:
            result = await asyncio.wait_for(
                _run_case(service, case), timeout=timeout_seconds
            )
        except Exception as exc:
            failures.append(f"{case.label}: {type(exc).__name__}: {exc}")
        else:
            results.append(result)
    if failures:
        raise RuntimeError("Live smoke failures:\n" + "\n".join(failures))
    return tuple(results)


async def _run_case(service: Any, case: SmokeCase) -> SmokeResult:
    company = await service.company(case.query, source=case.source)
    filings = await company.get_filings(source=case.source, limit=1)
    filing = filings.latest()
    if filing is None:
        raise RuntimeError(f"{company.id} returned no filings")
    return SmokeResult(
        label=case.label,
        company_id=company.id,
        filing_id=filing.id,
    )


async def _main() -> None:
    async with OpenFilingsService.from_settings() as service:
        results = await run_live_smoke(service)
    for result in results:
        print(f"PASS\t{result.label}\t{result.company_id}\t{result.filing_id}")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
