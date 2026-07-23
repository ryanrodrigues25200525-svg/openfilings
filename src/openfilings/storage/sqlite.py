"""Small SQLite cache for normalized metadata and compressed Markdown."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from datetime import UTC, datetime
from pathlib import Path

from openfilings.adapters.base import SourceDocument
from openfilings.models import (
    SUPPORTED_SOURCE_NAMES,
    CacheStats,
    Company,
    ExtractionQuality,
    Filing,
    FilingContent,
    FilingFinancials,
)


class SQLiteCache:
    """Synchronous cache optimized for small local metadata operations."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def __enter__(self) -> SQLiteCache:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def put_companies(self, companies: list[Company]) -> None:
        cached_at = _utc_now_iso()
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO companies (id, payload, cached_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    cached_at = excluded.cached_at
                """,
                [
                    (company.id, company.model_dump_json(), cached_at)
                    for company in companies
                ],
            )

    def put_filings(self, filings: list[Filing]) -> None:
        cached_at = _utc_now_iso()
        with self._connection:
            self._connection.executemany(
                """
                INSERT INTO filings (id, company_id, payload, cached_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    company_id = excluded.company_id,
                    payload = excluded.payload,
                    cached_at = excluded.cached_at
                """,
                [
                    (filing.id, filing.company_id, filing.model_dump_json(), cached_at)
                    for filing in filings
                ],
            )

    def get_company(self, company_id: str) -> Company | None:
        row = self._connection.execute(
            "SELECT payload FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        return Company.model_validate_json(row[0]) if row else None

    def search_companies(self, query: str, *, limit: int = 10) -> list[Company]:
        rows = self._connection.execute("SELECT payload FROM companies").fetchall()
        companies = [Company.model_validate_json(row[0]) for row in rows]
        wanted = query.strip().casefold()
        if not wanted:
            return []
        matches = [
            company for company in companies if _company_matches(company, wanted)
        ]
        matches.sort(
            key=lambda company: (_company_match_rank(company, wanted), company.name)
        )
        return matches[: max(0, limit)]

    def get_filing(self, filing_id: str) -> Filing | None:
        row = self._connection.execute(
            "SELECT payload FROM filings WHERE id = ?", (filing_id,)
        ).fetchone()
        return Filing.model_validate_json(row[0]) if row else None

    def list_filings(
        self,
        company_id: str,
        *,
        source: str | None = None,
        category: str | None = None,
        limit: int = 500,
    ) -> list[Filing]:
        rows = self._connection.execute(
            "SELECT payload FROM filings WHERE company_id = ?",
            (company_id,),
        ).fetchall()
        filings = [Filing.model_validate_json(row[0]) for row in rows]
        if source is not None:
            filings = [filing for filing in filings if filing.source == source]
        if category is not None:
            filings = [filing for filing in filings if filing.category == category]
        filings.sort(
            key=lambda filing: (
                filing.published_at.date()
                if filing.published_at
                else filing.filing_date,
                filing.published_at.isoformat() if filing.published_at else "",
                filing.id,
            ),
            reverse=True,
        )
        return filings[: max(0, limit)]

    def delete_filing(self, filing_id: str) -> None:
        with self._connection:
            for table, column in (
                ("filing_content", "filing_id"),
                ("source_documents", "filing_id"),
                ("filing_financials", "filing_id"),
                ("filings", "id"),
            ):
                self._connection.execute(
                    f"DELETE FROM {table} WHERE {column} = ?",
                    (filing_id,),
                )

    def get_market_state(self, key: str) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM market_state WHERE key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row else None

    def put_market_state(self, key: str, value: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO market_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, _utc_now_iso()),
            )

    def put_content(self, content: FilingContent) -> None:
        compressed = zlib.compress(content.markdown.encode("utf-8"), level=6)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO filing_content (
                    filing_id, markdown_zlib, source_url, media_type,
                    extraction_method, quality_json, sha256, extracted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(filing_id) DO UPDATE SET
                    markdown_zlib = excluded.markdown_zlib,
                    source_url = excluded.source_url,
                    media_type = excluded.media_type,
                    extraction_method = excluded.extraction_method,
                    quality_json = excluded.quality_json,
                    sha256 = excluded.sha256,
                    extracted_at = excluded.extracted_at
                """,
                (
                    content.filing_id,
                    compressed,
                    content.source_url,
                    content.media_type,
                    content.extraction_method,
                    content.quality.model_dump_json(),
                    content.sha256,
                    content.extracted_at.isoformat(),
                ),
            )

    def put_source_document(self, filing_id: str, document: SourceDocument) -> None:
        compressed = zlib.compress(document.data, level=6)
        digest = hashlib.sha256(document.data).hexdigest()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO source_documents (
                    filing_id, data_zlib, media_type, source_url, profile,
                    sha256, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(filing_id) DO UPDATE SET
                    data_zlib = excluded.data_zlib,
                    media_type = excluded.media_type,
                    source_url = excluded.source_url,
                    profile = excluded.profile,
                    sha256 = excluded.sha256,
                    cached_at = excluded.cached_at
                """,
                (
                    filing_id,
                    compressed,
                    document.media_type,
                    document.source_url,
                    document.profile,
                    digest,
                    _utc_now_iso(),
                ),
            )

    def get_source_document(self, filing_id: str) -> SourceDocument | None:
        row = self._connection.execute(
            """
            SELECT data_zlib, media_type, source_url, profile
            FROM source_documents
            WHERE filing_id = ?
            """,
            (filing_id,),
        ).fetchone()
        if row is None:
            return None
        return SourceDocument(
            data=zlib.decompress(row[0]),
            media_type=row[1],
            source_url=row[2],
            profile=row[3],
        )

    def get_content(self, filing_id: str) -> FilingContent | None:
        row = self._connection.execute(
            """
            SELECT markdown_zlib, source_url, media_type, extraction_method,
                   quality_json, sha256, extracted_at
            FROM filing_content
            WHERE filing_id = ?
            """,
            (filing_id,),
        ).fetchone()
        if row is None:
            return None

        markdown = zlib.decompress(row[0]).decode("utf-8")
        return FilingContent(
            filing_id=filing_id,
            markdown=markdown,
            source_url=row[1],
            media_type=row[2],
            extraction_method=row[3],
            quality=_quality_from_json(row[4]),
            sha256=row[5],
            extracted_at=datetime.fromisoformat(row[6]),
            from_cache=True,
        )

    def get_content_by_sha256(self, sha256: str) -> FilingContent | None:
        row = self._connection.execute(
            """
            SELECT filing_id, markdown_zlib, source_url, media_type,
                   extraction_method, quality_json, sha256, extracted_at
            FROM filing_content
            WHERE sha256 = ?
            LIMIT 1
            """,
            (sha256,),
        ).fetchone()
        if row is None:
            return None
        return FilingContent(
            filing_id=row[0],
            markdown=zlib.decompress(row[1]).decode("utf-8"),
            source_url=row[2],
            media_type=row[3],
            extraction_method=row[4],
            quality=_quality_from_json(row[5]),
            sha256=row[6],
            extracted_at=datetime.fromisoformat(row[7]),
            from_cache=True,
        )

    def put_financials(self, financials: FilingFinancials) -> None:
        compressed = zlib.compress(
            financials.model_dump_json().encode("utf-8"), level=6
        )
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO filing_financials (
                    filing_id, payload_zlib, sha256, extracted_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(filing_id) DO UPDATE SET
                    payload_zlib = excluded.payload_zlib,
                    sha256 = excluded.sha256,
                    extracted_at = excluded.extracted_at
                """,
                (
                    financials.filing_id,
                    compressed,
                    financials.sha256,
                    financials.extracted_at.isoformat(),
                ),
            )

    def get_financials(self, filing_id: str) -> FilingFinancials | None:
        row = self._connection.execute(
            "SELECT payload_zlib FROM filing_financials WHERE filing_id = ?",
            (filing_id,),
        ).fetchone()
        if row is None:
            return None
        financials = FilingFinancials.model_validate_json(
            zlib.decompress(row[0]).decode("utf-8")
        )
        return financials.model_copy(update={"from_cache": True})

    def stats(self) -> CacheStats:
        companies = self._count("companies")
        filings = self._count("filings")
        documents = self._count("filing_content")
        source_documents = self._count("source_documents")
        financial_reports = self._count("filing_financials")
        row = self._connection.execute(
            "SELECT COALESCE(SUM(LENGTH(markdown_zlib)), 0) FROM filing_content"
        ).fetchone()
        compressed_bytes = int(row[0]) if row else 0
        row = self._connection.execute(
            "SELECT COALESCE(SUM(LENGTH(data_zlib)), 0) FROM source_documents"
        ).fetchone()
        compressed_source_bytes = int(row[0]) if row else 0
        row = self._connection.execute(
            "SELECT COALESCE(SUM(LENGTH(payload_zlib)), 0) FROM filing_financials"
        ).fetchone()
        compressed_financial_bytes = int(row[0]) if row else 0
        database_bytes = sum(
            path.stat().st_size
            for path in (
                self._path,
                Path(f"{self._path}-wal"),
                Path(f"{self._path}-shm"),
            )
            if path.exists()
        )
        return CacheStats(
            companies=companies,
            filings=filings,
            documents=documents,
            source_documents=source_documents,
            financial_reports=financial_reports,
            compressed_content_bytes=compressed_bytes,
            compressed_source_bytes=compressed_source_bytes,
            compressed_financial_bytes=compressed_financial_bytes,
            database_bytes=database_bytes,
        )

    def prune_content(self, max_bytes: int, *, vacuum: bool = False) -> int:
        if max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
        rows = self._connection.execute(
            """
            SELECT filing_id, LENGTH(markdown_zlib)
            FROM filing_content
            ORDER BY extracted_at ASC, filing_id ASC
            """
        ).fetchall()
        total_bytes = sum(int(row[1]) for row in rows)
        remove_ids: list[str] = []
        for filing_id, content_bytes in rows:
            if total_bytes <= max_bytes:
                break
            remove_ids.append(str(filing_id))
            total_bytes -= int(content_bytes)

        if remove_ids:
            with self._connection:
                self._connection.executemany(
                    "DELETE FROM filing_content WHERE filing_id = ?",
                    [(filing_id,) for filing_id in remove_ids],
                )
        if vacuum:
            self.vacuum()
        return len(remove_ids)

    def prune_financials(self, max_bytes: int, *, vacuum: bool = False) -> int:
        if max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
        rows = self._connection.execute(
            """
            SELECT filing_id, LENGTH(payload_zlib)
            FROM filing_financials
            ORDER BY extracted_at ASC, filing_id ASC
            """
        ).fetchall()
        total_bytes = sum(int(row[1]) for row in rows)
        remove_ids: list[str] = []
        for filing_id, payload_bytes in rows:
            if total_bytes <= max_bytes:
                break
            remove_ids.append(str(filing_id))
            total_bytes -= int(payload_bytes)
        if remove_ids:
            with self._connection:
                self._connection.executemany(
                    "DELETE FROM filing_financials WHERE filing_id = ?",
                    [(filing_id,) for filing_id in remove_ids],
                )
        if vacuum:
            self.vacuum()
        return len(remove_ids)

    def prune_source_documents(self, max_bytes: int, *, vacuum: bool = False) -> int:
        if max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
        rows = self._connection.execute(
            """
            SELECT filing_id, LENGTH(data_zlib)
            FROM source_documents
            ORDER BY cached_at ASC, filing_id ASC
            """
        ).fetchall()
        total_bytes = sum(int(row[1]) for row in rows)
        remove_ids: list[str] = []
        for filing_id, document_bytes in rows:
            if total_bytes <= max_bytes:
                break
            remove_ids.append(str(filing_id))
            total_bytes -= int(document_bytes)
        if remove_ids:
            with self._connection:
                self._connection.executemany(
                    "DELETE FROM source_documents WHERE filing_id = ?",
                    [(filing_id,) for filing_id in remove_ids],
                )
        if vacuum:
            self.vacuum()
        return len(remove_ids)

    def vacuum(self) -> None:
        self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._connection.execute("VACUUM")

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    cached_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS filings (
                    id TEXT PRIMARY KEY,
                    company_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    cached_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS filings_company_id_idx
                    ON filings(company_id);

                CREATE TABLE IF NOT EXISTS market_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS filing_content (
                    filing_id TEXT PRIMARY KEY,
                    markdown_zlib BLOB NOT NULL,
                    source_url TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    extraction_method TEXT NOT NULL,
                    quality_json TEXT,
                    sha256 TEXT NOT NULL,
                    extracted_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS filing_content_sha256_idx
                    ON filing_content(sha256);

                CREATE TABLE IF NOT EXISTS source_documents (
                    filing_id TEXT PRIMARY KEY,
                    data_zlib BLOB NOT NULL,
                    media_type TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    profile TEXT,
                    sha256 TEXT NOT NULL,
                    cached_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS filing_financials (
                    filing_id TEXT PRIMARY KEY,
                    payload_zlib BLOB NOT NULL,
                    sha256 TEXT NOT NULL,
                    extracted_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS filing_financials_sha256_idx
                    ON filing_financials(sha256);
                """
            )
            columns = {
                row[1]
                for row in self._connection.execute(
                    "PRAGMA table_info(filing_content)"
                ).fetchall()
            }
            if "quality_json" not in columns:
                self._connection.execute(
                    "ALTER TABLE filing_content ADD COLUMN quality_json TEXT"
                )
            self._remove_unsupported_source_records()

    def _remove_unsupported_source_records(self) -> None:
        company_ids = self._unsupported_company_ids()
        filing_ids = self._unsupported_filing_ids(company_ids)
        self._delete_records("filing_content", "filing_id", filing_ids)
        self._delete_records("source_documents", "filing_id", filing_ids)
        self._delete_records("filing_financials", "filing_id", filing_ids)
        self._delete_records("filings", "id", filing_ids)
        self._delete_records("companies", "id", company_ids)

    def _unsupported_company_ids(self) -> set[str]:
        rows = self._connection.execute("SELECT id, payload FROM companies").fetchall()
        return {
            str(company_id)
            for company_id, payload in rows
            if not _company_payload_is_supported(str(payload))
        }

    def _unsupported_filing_ids(self, company_ids: set[str]) -> set[str]:
        rows = self._connection.execute(
            "SELECT id, company_id, payload FROM filings"
        ).fetchall()
        return {
            str(filing_id)
            for filing_id, company_id, payload in rows
            if str(company_id) in company_ids
            or not _filing_payload_is_supported(str(payload))
        }

    def _delete_records(self, table: str, key: str, record_ids: set[str]) -> None:
        if not record_ids:
            return
        self._connection.executemany(
            f"DELETE FROM {table} WHERE {key} = ?",
            [(record_id,) for record_id in record_ids],
        )

    def _count(self, table: str) -> int:
        if table not in {
            "companies",
            "filings",
            "filing_content",
            "source_documents",
            "filing_financials",
        }:
            raise ValueError("Unsupported cache table")
        row = self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _quality_from_json(value: str | None) -> ExtractionQuality:
    if not value:
        return ExtractionQuality()
    try:
        return ExtractionQuality.model_validate(json.loads(value))
    except (ValueError, TypeError):
        return ExtractionQuality(
            score=50,
            status="degraded",
            warnings=("invalid_cached_quality_metadata",),
        )


def _company_matches(company: Company, query: str) -> bool:
    return any(query in value.casefold() for value in _company_search_values(company))


def _company_match_rank(company: Company, query: str) -> int:
    return (
        0
        if any(query == value.casefold() for value in _company_search_values(company))
        else 1
    )


def _company_search_values(company: Company) -> tuple[str, ...]:
    return (
        company.id,
        company.name,
        company.lei or "",
        company.ticker or "",
        company.local_code or "",
    )


def _company_payload_is_supported(payload: str) -> bool:
    try:
        sources = set(json.loads(payload)["sources"])
    except (KeyError, TypeError, ValueError):
        return False
    return bool(sources) and sources <= SUPPORTED_SOURCE_NAMES


def _filing_payload_is_supported(payload: str) -> bool:
    try:
        source = json.loads(payload)["source"]
    except (KeyError, TypeError, ValueError):
        return False
    return source in SUPPORTED_SOURCE_NAMES
