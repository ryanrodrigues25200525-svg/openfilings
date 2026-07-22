from __future__ import annotations

import pytest

from openfilings.config import Settings
from openfilings.exceptions import ConfigurationError


def test_ocr_and_cache_settings_are_loaded_from_environment(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENFILINGS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENFILINGS_OCR_MODE", "never")
    monkeypatch.setenv("OPENFILINGS_OCR_LANGUAGE", "eng+fra")
    monkeypatch.setenv("OPENFILINGS_OCR_DPI", "300")
    monkeypatch.setenv("OPENFILINGS_OCR_MAX_PAGES", "80")
    monkeypatch.setenv("OPENFILINGS_CACHE_MAX_MB", "256")

    settings = Settings.from_env()

    assert settings.ocr_mode == "never"
    assert settings.ocr_language == "eng+fra"
    assert settings.ocr_dpi == 300
    assert settings.ocr_max_pages == 80
    assert settings.cache_max_mb == 256


def test_invalid_ocr_mode_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("OPENFILINGS_OCR_MODE", "sometimes")

    with pytest.raises(ConfigurationError, match="auto, never, or always"):
        Settings.from_env()
