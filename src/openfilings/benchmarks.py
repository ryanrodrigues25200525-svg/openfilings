"""Small, traceable live accuracy benchmarks for authoritative filings.

Smoke checks establish that a regulator contract still responds. These checks
go one step further: they pin a handful of figures transcribed from a specific
public filing and fail if the normalized output changes. They are deliberately
small and reviewed fixtures, not a claim that two issuers represent a market.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from openfilings.models import StatementType


@dataclass(frozen=True, slots=True)
class ReferenceFact:
    code: str
    statement_type: StatementType
    period_end: date
    value: Decimal
    unit: str
    concept: str
    provenance: str = "tagged_xbrl"
    confidence: int = 100


@dataclass(frozen=True, slots=True)
class AccuracyBenchmark:
    label: str
    filing_id: str
    filing_url: str
    facts: tuple[ReferenceFact, ...]


@dataclass(frozen=True, slots=True)
class AccuracyResult:
    label: str
    filing_id: str
    facts_checked: int


# Values below are transcribed from the stated public annual-report packages,
# in the filing currency and units. Add a benchmark only after an independent
# source-document review; never populate this list from the extractor output.
ACCURACY_BENCHMARKS = (
    AccuracyBenchmark(
        label="UK FCA NSM Tesco FY2026 balance sheet",
        filing_id="uk_nsm_NI-000144970",
        filing_url=(
            "https://data.fca.org.uk/artefacts/NSM/DirectUpload/NI-000144970/"
            "NI-000144970_2138002P5RNKC5W2JZ46-2026-02-28.zip"
        ),
        facts=(
            ReferenceFact(
                "total_assets",
                "balance_sheet",
                date(2026, 2, 28),
                Decimal("39474000000"),
                "iso4217:GBP",
                "derived:current_assets+noncurrent_assets",
                "derived",
            ),
            ReferenceFact(
                "total_equity",
                "balance_sheet",
                date(2026, 2, 28),
                Decimal("11457000000"),
                "iso4217:GBP",
                "ifrs-full:Equity",
            ),
        ),
    ),
    AccuracyBenchmark(
        label="ESEF ASML FY2025 balance sheet",
        filing_id="nl_esef_23718",
        filing_url="https://filings.xbrl.org/filing/23718/",
        facts=(
            ReferenceFact(
                "total_assets",
                "balance_sheet",
                date(2025, 12, 31),
                Decimal("55576800000"),
                "iso4217:EUR",
                "ifrs-full:Assets",
            ),
            ReferenceFact(
                "total_equity",
                "balance_sheet",
                date(2025, 12, 31),
                Decimal("24185000000"),
                "iso4217:EUR",
                "ifrs-full:Equity",
            ),
        ),
    ),
)


async def run_live_accuracy_benchmarks(
    service: Any,
    *,
    benchmarks: tuple[AccuracyBenchmark, ...] = ACCURACY_BENCHMARKS,
    timeout_seconds: float = 240.0,
) -> tuple[AccuracyResult, ...]:
    """Fetch benchmarked filings and compare exact, source-reviewed facts."""

    results: list[AccuracyResult] = []
    failures: list[str] = []
    for benchmark in benchmarks:
        try:
            financials = await asyncio.wait_for(
                service.get_filing_financials(benchmark.filing_id, refresh=True),
                timeout=timeout_seconds,
            )
            _assert_reference_facts(financials, benchmark)
        except Exception as exc:
            failures.append(f"{benchmark.label}: {type(exc).__name__}: {exc}")
        else:
            results.append(
                AccuracyResult(
                    benchmark.label, benchmark.filing_id, len(benchmark.facts)
                )
            )
    if failures:
        raise RuntimeError("Live accuracy benchmark failures:\n" + "\n".join(failures))
    return tuple(results)


def _assert_reference_facts(financials: Any, benchmark: AccuracyBenchmark) -> None:
    values = {
        (statement.statement_type, item.code, value.period.end_date): (
            item,
            value,
        )
        for statement in financials.statements
        for item in statement.line_items
        for value in item.values
    }
    for fact in benchmark.facts:
        actual = values.get((fact.statement_type, fact.code, fact.period_end))
        if actual is None:
            raise AssertionError(
                f"missing {fact.statement_type}/{fact.code} at {fact.period_end} "
                f"(review {benchmark.filing_url})"
            )
        item, value = actual
        if (
            value.value != fact.value
            or value.unit != fact.unit
            or item.concept != fact.concept
            or value.provenance != fact.provenance
            or value.confidence != fact.confidence
        ):
            raise AssertionError(
                f"{fact.code} at {fact.period_end}: expected {fact.value} "
                f"{fact.unit} {fact.concept}, got {value.value} {value.unit} "
                f"{item.concept} "
                f"(review {benchmark.filing_url})"
            )


async def _main() -> None:
    from openfilings.service import OpenFilingsService

    async with OpenFilingsService.from_settings() as service:
        results = await run_live_accuracy_benchmarks(service)
    for result in results:
        print(f"PASS\t{result.label}\t{result.filing_id}\t{result.facts_checked} facts")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
