from __future__ import annotations

import json

from typer.testing import CliRunner

from openfilings.cli import app


def test_inspect_document_reports_quality_and_timing(tmp_path, monkeypatch) -> None:
    source = tmp_path / "announcement.html"
    source.write_text(
        "<html><body><h1>Results</h1><p>"
        + ("Revenue increased. " * 30)
        + "</p></body></html>",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["inspect-document", source.name])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["extraction_method"] == "markdownify"
    assert payload["quality"]["status"] == "good"
    assert payload["elapsed_seconds"] >= 0
