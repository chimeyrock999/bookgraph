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


def test_llmwiki_serve_uncompiled_wiki(tmp_path: Path) -> None:
    # Realistic flow: init creates an empty wiki/ skeleton but no pages have been
    # compiled yet, so serving must fail cleanly instead of serving an empty wiki.
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path)])

    assert result.exit_code != 0
    assert "No compiled wiki found" in result.output


def test_llmwiki_serve_print_emits_command(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    # Simulate a compiled wiki so the content guard passes.
    (tmp_path / "wiki" / "books").mkdir(parents=True, exist_ok=True)
    (tmp_path / "wiki" / "books" / "README.md").write_text("# compiled\n")

    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path), "--print"])

    assert result.exit_code == 0, result.output
    wiki_dir = (tmp_path / "wiki").resolve()
    assert result.output.strip() == f"llmwiki serve {wiki_dir}"
