"""Shared source-adapter value objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """Downloaded source bytes plus the provenance needed for extraction."""

    data: bytes
    media_type: str
    source_url: str
    profile: str | None = None
