from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app


def test_init_creates_pluggable_project_layout(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, [str(tmp_path)])

    assert result.exit_code == 0
    expected_dirs = [
        "sources/inbox",
        "sources/parsed",
        "sources/sections",
        "wiki/concepts",
        "wiki/comparisons",
        "wiki/daily",
        "indexes",
        "reading_plans",
        "runs",
    ]
    for rel in expected_dirs:
        assert (tmp_path / rel).is_dir(), rel
    assert (tmp_path / "bookgraph.toml").is_file()
