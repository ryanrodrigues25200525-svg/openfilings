"""Environment-backed application settings."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from openfilings.exceptions import ConfigurationError
from openfilings.models import OcrMode

_OCR_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z0-9_.+-]+$")


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings with deliberately conservative resource defaults."""

    companies_house_api_key: str
    edinet_api_key: str
    data_dir: Path
    request_timeout_seconds: float = 30.0
    max_retries: int = 2
    ocr_mode: OcrMode = "auto"
    ocr_language: str = "eng"
    ocr_dpi: int = 200
    ocr_max_pages: int = 250
    ocr_executable: str = "tesseract"
    cache_max_mb: int = 512

    @classmethod
    def from_env(cls, *, require_api_key: bool = False) -> Settings:
        api_key = os.getenv("COMPANIES_HOUSE_API_KEY", "").strip()
        if require_api_key and not api_key:
            raise ConfigurationError(
                "COMPANIES_HOUSE_API_KEY is required for Companies House requests."
            )

        data_dir_value = os.getenv("OPENFILINGS_DATA_DIR", ".openfilings")
        data_dir = Path(data_dir_value).expanduser().resolve()
        ocr_mode = os.getenv("OPENFILINGS_OCR_MODE", "auto").strip().casefold()
        if ocr_mode not in {"auto", "never", "always"}:
            raise ConfigurationError(
                "OPENFILINGS_OCR_MODE must be auto, never, or always."
            )
        ocr_language = os.getenv("OPENFILINGS_OCR_LANGUAGE", "eng").strip()
        if not _OCR_LANGUAGE_PATTERN.fullmatch(ocr_language):
            raise ConfigurationError("OPENFILINGS_OCR_LANGUAGE is invalid.")

        return cls(
            companies_house_api_key=api_key,
            edinet_api_key=os.getenv("EDINET_API_KEY", "").strip(),
            data_dir=data_dir,
            ocr_mode=cast(OcrMode, ocr_mode),
            ocr_language=ocr_language,
            ocr_dpi=_bounded_int_env("OPENFILINGS_OCR_DPI", 200, 72, 600),
            ocr_max_pages=_bounded_int_env("OPENFILINGS_OCR_MAX_PAGES", 250, 1, 2_000),
            ocr_executable=os.getenv(
                "OPENFILINGS_TESSERACT_EXECUTABLE", "tesseract"
            ).strip()
            or "tesseract",
            cache_max_mb=_bounded_int_env("OPENFILINGS_CACHE_MAX_MB", 512, 16, 100_000),
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "openfilings.sqlite3"


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return value
