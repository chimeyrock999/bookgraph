from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app


def test_init_creates_pluggable_project_layout(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["init", str(tmp_path)])

    assert result.exit_code == 0
    expected_dirs = [
        "sources/inbox",
        "sources/parsed",
        "sources/sections",
        "wiki/concepts",
        "wiki/books",
        "wiki/comparisons",
        "wiki/daily",
        "indexes",
        "reading_plans",
        "runs",
    ]
    for rel in expected_dirs:
        assert (tmp_path / rel).is_dir(), rel
    assert (tmp_path / "bookgraph.toml").is_file()


def test_init_accepts_output_alias_for_workspace_path(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"

    result = runner.invoke(app, ["init", "--output", str(workspace)])

    assert result.exit_code == 0
    assert (workspace / "bookgraph.toml").is_file()
    assert "output_root" in (workspace / "bookgraph.toml").read_text()


def test_paths_prints_all_workspace_output_locations(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["init", str(tmp_path)])

    result = runner.invoke(app, ["paths", str(tmp_path)])

    assert result.exit_code == 0
    for expected in [
        "sources.inbox",
        "sources.parsed",
        "sources.sections",
        "wiki.root",
        "wiki.books",
        "indexes.root",
        "reading_plans.root",
        "runs.root",
    ]:
        assert expected in result.output
    assert str(tmp_path / "sources" / "sections") in result.output
