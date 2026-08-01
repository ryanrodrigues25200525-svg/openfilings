"""Guard against MCP tool documentation drift.

Ten of twenty-two tools were undocumented in the README before this test
existed - including data_quality_report, the tool an agent needs to decide
whether to trust a figure. This asserts every @mcp.tool() in server.py has a
corresponding backtick-quoted entry in the README's MCP tools section, so a
new tool cannot ship silently undocumented again.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_every_mcp_tool_is_documented_in_the_readme() -> None:
    server_source = (_REPO_ROOT / "src/openfilings/server.py").read_text()
    tool_names = set(
        re.findall(r"@mcp\.tool\(\)\s*\nasync def ([a-z_]+)", server_source)
    )
    assert tool_names, "expected to find at least one @mcp.tool() in server.py"

    readme = (_REPO_ROOT / "README.md").read_text()
    section = readme.split("## MCP tools", 1)[1].split("\n## ", 1)[0]
    documented_names = set(re.findall(r"`([a-z_]+)\(", section))

    missing = tool_names - documented_names
    assert not missing, (
        f"MCP tool(s) missing from the README's MCP tools section: {sorted(missing)}"
    )
