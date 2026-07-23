"""Minimal import check run against built wheel and source distributions."""

import asyncio

from openfilings import OpenFilings, OpenFilingsService
from openfilings.cli import app
from openfilings.models import SUPPORTED_SOURCE_NAMES
from openfilings.server import mcp

assert OpenFilings is OpenFilingsService
assert app.info.name == "openfilings"
assert "sgx" in SUPPORTED_SOURCE_NAMES
assert {"bmv", "nse", "sedar", "smv", "sfc"} <= SUPPORTED_SOURCE_NAMES
assert {
    "filing_outline",
    "filing_read",
    "filing_search",
    "sedar_filing_import",
} <= {tool.name for tool in asyncio.run(mcp.list_tools())}
print("OpenFilings package smoke test passed")
