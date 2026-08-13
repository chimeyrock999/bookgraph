from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.cli import app

_FASTMCP_INSTALLED = find_spec("fastmcp") is not None


def test_mcp_command_is_registered() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "mcp" in result.output


@pytest.mark.skipif(
    _FASTMCP_INSTALLED,
    reason="guard only fires when the optional 'mcp' extra is absent",
)
def test_mcp_command_reports_missing_extra(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["mcp", str(tmp_path)])

    assert result.exit_code != 0
    assert "mcp" in result.output
    assert "extra" in result.output
