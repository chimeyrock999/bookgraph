from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app


def _write_sections_manifest(workspace: Path, doc_id: str) -> Path:
    sections_dir = workspace / "sources" / "sections" / doc_id
    sections_dir.mkdir(parents=True)
    manifest = sections_dir / "sections.jsonl"
    manifest.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": f"{doc_id}.intro",
                        "doc_id": doc_id,
                        "title": "Intro",
                        "level": 1,
                        "heading_path": ["Intro"],
                        "page_start": 1,
                        "page_end": 2,
                        "text": "Hello wiki.",
                        "prev_id": None,
                        "next_id": f"{doc_id}.chapter-1",
                        "block_ids": ["b1"],
                        "metadata": {},
                    }
                ),
                json.dumps(
                    {
                        "id": f"{doc_id}.chapter-1",
                        "doc_id": doc_id,
                        "title": "Chapter 1",
                        "level": 2,
                        "heading_path": ["Intro", "Chapter 1"],
                        "page_start": 3,
                        "page_end": 4,
                        "text": "More text.",
                        "prev_id": f"{doc_id}.intro",
                        "next_id": None,
                        "block_ids": ["b2"],
                        "metadata": {},
                    }
                ),
            ]
        )
        + "\n"
    )
    return manifest


def test_wiki_compile_reads_sections_and_writes_wiki_book(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    _write_sections_manifest(workspace, "deep-work")

    result = runner.invoke(app, ["wiki", "compile", str(workspace), "deep-work"])

    assert result.exit_code == 0, result.output
    book_dir = workspace / "wiki" / "books" / "deep-work"
    assert (book_dir / "sections" / "deep-work.intro.md").is_file()
    index = (book_dir / "README.md").read_text()
    assert "# deep-work" in index
    assert "- [Intro](sections/deep-work.intro.md)" in index
    intro = (book_dir / "sections" / "deep-work.intro.md").read_text()
    assert "id: \"deep-work.intro\"" in intro
    assert "# Intro" in intro
    assert "Hello wiki." in intro
    assert not (workspace / "runs" / "cli-placeholders" / "wiki-compile-deep-work.json").exists()
    assert "backend: llmwiki" in result.output
    assert "sections: 2" in result.output
    assert f"wiki: {book_dir}" in result.output


def test_wiki_compile_reports_missing_sections_manifest(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["wiki", "compile", str(workspace), "missing"])

    assert result.exit_code != 0
    assert "Sections manifest not found" in result.output
    assert not (workspace / "wiki" / "books" / "missing").exists()


def test_wiki_compile_dry_run_keeps_placeholder_contract(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["wiki", "compile", str(workspace), "deep-work", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert (workspace / "runs" / "cli-placeholders" / "wiki-compile-deep-work.json").is_file()
    assert not (workspace / "wiki" / "books" / "deep-work").exists()
    assert "Backend not run" in result.output
