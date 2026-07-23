"""Bounded live checks for keyless regulator integrations.

Beyond "did search/filings return something," each financial-checked case
fetches the latest filing's financials and verifies the fundamental
balance-sheet identity (assets = liabilities + equity) holds. Spot-checking
by hand is how every real bug this project has found actually surfaced -
this turns that into a repeatable, scheduled check instead of something
that only happens when someone thinks to look.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from openfilings.service import OpenFilingsService

_IDENTITY_CODES = ("total_assets", "total_liabilities", "total_equity")
# A tiny relative tolerance absorbs rounding noise in source data (seen in
# real filings, e.g. a few cents on figures in the hundreds of billions) -
# not genuine reconciliation gaps, which are orders of magnitude larger.
_IDENTITY_TOLERANCE = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class SmokeCase:
    label: str
    query: str
    source: str
    check_financials: bool = True


@dataclass(frozen=True, slots=True)
class SmokeResult:
    label: str
    company_id: str
    filing_id: str | None
    identity_check: str


SMOKE_CASES = (
    SmokeCase("UK FCA NSM", "Tesco PLC", "fca_nsm"),
    SmokeCase("ESEF Netherlands", "ASML", "esef"),
    SmokeCase("ESEF France", "TotalEnergies", "esef"),
    SmokeCase("ESEF Spain", "Iberdrola", "esef"),
    SmokeCase("ESEF Italy", "Enel", "esef"),
    SmokeCase("ESEF Denmark", "Novo Nordisk", "esef"),
    # Regression guard: AB Volvo's disposal-group held-for-sale liabilities
    # bucket previously fell outside the current+noncurrent derivation.
    SmokeCase("ESEF Sweden", "Aktiebolaget Volvo", "esef"),
    SmokeCase("ESEF Finland", "Nokia", "esef"),
    SmokeCase("ESEF Norway", "Equinor", "esef"),
    SmokeCase("ESEF Poland", "Orlen", "esef"),
    SmokeCase("ESEF Belgium", "KBC", "esef"),
    # OMV's recent filings omit a tagged total_liabilities line item.
    SmokeCase("ESEF Austria", "Verbund", "esef"),
    SmokeCase("ESEF Luxembourg", "ArcelorMittal", "esef"),
    SmokeCase("ESEF Portugal", "EDP", "esef"),
    SmokeCase("Brazil CVM", "Petrobras", "cvm"),
    # Regression guard: Keppel's "net assets" presentation previously
    # misread its non-current liabilities subtotal.
    SmokeCase("Singapore SGX", "Keppel Ltd", "sgx"),
    SmokeCase("Mexico BMV", "AMX", "bmv"),
    SmokeCase("India NSE", "RELIANCE", "nse"),
    SmokeCase("Peru SMV", "Alicorp", "smv"),
    # A bank specifically, to exercise the CUIF structured balance-sheet
    # path rather than just the PDF-covered income statement.
    SmokeCase("Colombia SFC", "Banco de Bogota", "sfc"),
    # SEDAR+ has no public filing-search API by design; only company
    # discovery is keyless, so financials aren't checked here.
    SmokeCase("Canada TSX", "Shopify", "sedar", check_financials=False),
    # Company search is keyless; the filing API needs a free EDINET key
    # not available in CI, so financials aren't checked here.
    SmokeCase("Japan EDINET", "Toyota", "edinet", check_financials=False),
)


async def run_live_smoke(
    service: Any,
    *,
    cases: tuple[SmokeCase, ...] = SMOKE_CASES,
    timeout_seconds: float = 240.0,
) -> tuple[SmokeResult, ...]:
    """Check one company, its latest filing, and (where applicable) that
    filing's balance-sheet identity per keyless source family."""

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
    if not case.check_financials:
        return SmokeResult(case.label, company.id, None, "search_only")
    filings = await company.get_filings(source=case.source, limit=5)
    if filings.latest() is None:
        raise RuntimeError(f"{company.id} returned no filings")
    # A source can list several recent "accounts"-category disclosures
    # where only some actually carry extractable data (e.g. FCA NSM tags a
    # short announcement pointing at the full report the same way it tags
    # the report itself). Any one of the most recent few succeeding proves
    # the source is healthy; only fail if none of them do.
    last_error: Exception | None = None
    for filing in filings[:3]:
        try:
            financials = await filing.financials(refresh=True)
        except Exception as exc:  # reported in the final error, not swallowed
            last_error = exc
            continue
        identity = _balance_sheet_identity(financials)
        if identity.startswith("GAP"):
            raise RuntimeError(f"{case.label} ({company.id}/{filing.id}): {identity}")
        return SmokeResult(case.label, company.id, filing.id, identity)
    raise RuntimeError(
        f"{case.label} ({company.id}): no usable financials in the "
        f"{min(3, len(filings))} most recent filings ({last_error})"
    )


def _balance_sheet_identity(financials: Any) -> str:
    balance = financials.balance_sheet()
    if balance is None:
        return "not_applicable (no balance sheet extracted)"
    by_code = {
        item.code: item.values[0].value for item in balance.line_items if item.values
    }
    missing = [code for code in _IDENTITY_CODES if code not in by_code]
    if missing:
        return f"not_applicable (missing {', '.join(missing)})"
    assets = by_code["total_assets"]
    combined = by_code["total_liabilities"] + by_code["total_equity"]
    tolerance = abs(assets) * _IDENTITY_TOLERANCE
    if abs(combined - assets) <= tolerance:
        return "held"
    return f"GAP: liabilities+equity={combined} vs assets={assets}"


async def _main() -> None:
    async with OpenFilingsService.from_settings() as service:
        results = await run_live_smoke(service)
    for result in results:
        print(
            f"PASS\t{result.label}\t{result.company_id}\t"
            f"{result.filing_id}\t{result.identity_check}"
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
