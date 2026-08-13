from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app

runner = CliRunner()


def test_llmwiki_serve_is_registered() -> None:
    result = runner.invoke(app, ["llmwiki", "--help"])

    assert result.exit_code == 0, result.output
    assert "serve" in result.output


def test_llmwiki_serve_missing_workspace(tmp_path: Path) -> None:
    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path / "nope")])

    assert result.exit_code != 0
    assert "Workspace not found" in result.output


def test_llmwiki_serve_missing_wiki_dir(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    # `init` creates wiki/, so remove it to exercise the missing-wiki branch.
    wiki_dir = tmp_path / "wiki"
    for child in sorted(wiki_dir.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    wiki_dir.rmdir()

    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path)])

    assert result.exit_code != 0
    assert "Wiki directory not found" in result.output


def test_llmwiki_serve_print_emits_command(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path), "--print"])

    assert result.exit_code == 0, result.output
    wiki_dir = (tmp_path / "wiki").resolve()
    assert result.output.strip() == f"llmwiki serve {wiki_dir}"
