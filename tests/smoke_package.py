"""Minimal import check run against built wheel and source distributions."""

from openfilings import OpenFilings, OpenFilingsService
from openfilings.cli import app
from openfilings.models import SUPPORTED_SOURCE_NAMES

assert OpenFilings is OpenFilingsService
assert app.info.name == "openfilings"
assert "sgx" in SUPPORTED_SOURCE_NAMES
print("OpenFilings package smoke test passed")
