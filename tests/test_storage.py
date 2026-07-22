from __future__ import annotations

import hashlib
import sqlite3

from openfilings.models import ExtractionQuality, FilingContent
from openfilings.storage.sqlite import SQLiteCache


def test_markdown_round_trips_as_compressed_content(tmp_path) -> None:
    cache = SQLiteCache(tmp_path / "cache.sqlite3")
    markdown = "# Filing\n\n" + ("revenue | 100\n" * 10_000)
    content = FilingContent(
        filing_id="uk_1_tx",
        markdown=markdown,
        source_url="https://example.test/document",
        sha256=hashlib.sha256(b"pdf").hexdigest(),
        quality=ExtractionQuality(
            score=72,
            status="good",
            character_count=len(markdown),
            page_count=10,
            warnings=("minor_encoding_noise",),
        ),
    )

    cache.put_content(content)
    restored = cache.get_content(content.filing_id)

    assert restored is not None
    assert restored.markdown == markdown
    assert restored.from_cache is True
    assert restored.quality.score == 72
    assert restored.quality.page_count == 10
    assert restored.quality.warnings == ("minor_encoding_noise",)
    assert (tmp_path / "cache.sqlite3").stat().st_size < len(markdown.encode())

    stats = cache.stats()
    assert stats.documents == 1
    assert stats.compressed_content_bytes > 0
    assert stats.database_bytes > 0

    removed = cache.prune_content(0, vacuum=True)
    assert removed == 1
    assert cache.stats().documents == 0
    cache.close()


def test_existing_cache_is_migrated_with_quality_metadata(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE filing_content (
            filing_id TEXT PRIMARY KEY,
            markdown_zlib BLOB NOT NULL,
            source_url TEXT NOT NULL,
            media_type TEXT NOT NULL,
            extraction_method TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            extracted_at TEXT NOT NULL
        )
        """
    )
    connection.close()

    cache = SQLiteCache(path)
    content = FilingContent(
        filing_id="legacy-test",
        markdown="# Migrated",
        source_url="https://example.test/migrated",
        sha256=hashlib.sha256(b"migrated").hexdigest(),
    )
    cache.put_content(content)
    restored = cache.get_content(content.filing_id)
    cache.close()

    assert restored is not None
    assert restored.quality.warnings == ("quality_not_recorded",)
