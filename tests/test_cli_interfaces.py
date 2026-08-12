from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app


def _init_workspace(tmp_path: Path) -> CliRunner:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return runner


def _placeholder(tmp_path: Path, name: str) -> dict[str, object]:
    return json.loads((tmp_path / "runs" / "cli-placeholders" / f"{name}.json").read_text())


def test_parse_book_writes_only_placeholder_contract(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["parse-book", str(tmp_path), "deep-work", "--parser", "mineru"])

    assert result.exit_code == 0, result.output
    payload = _placeholder(tmp_path, "parse-book-deep-work")
    assert payload["command"] == "parse-book"
    assert payload["book_id"] == "deep-work"
    assert payload["parser"] == "mineru"
    assert payload["backend_not_run"] is True
    assert not (tmp_path / "sources" / "parsed" / "deep-work").exists()
    assert "Backend not run" in result.output


def test_segment_writes_only_placeholder_contract(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["segment", str(tmp_path), "deep-work", "--segmenter", "heading"])

    assert result.exit_code == 0, result.output
    payload = _placeholder(tmp_path, "segment-deep-work")
    assert payload["command"] == "segment"
    assert payload["doc_id"] == "deep-work"
    assert payload["segmenter"] == "heading"
    assert payload["outputs"] == {
        "sections_manifest": str(
            tmp_path / "sources" / "sections" / "deep-work" / "sections.jsonl"
        ),
        "sections_dir": str(tmp_path / "sources" / "sections" / "deep-work"),
    }
    assert not (tmp_path / "sources" / "sections" / "deep-work").exists()


def test_wiki_compile_writes_only_placeholder_contract(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["wiki", "compile", str(tmp_path), "deep-work"])

    assert result.exit_code == 0, result.output
    payload = _placeholder(tmp_path, "wiki-compile-deep-work")
    assert payload["command"] == "wiki compile"
    assert payload["backend"] == "llmwiki"
    assert payload["backend_not_run"] is True
    assert not (tmp_path / "wiki" / "books" / "deep-work").exists()


def test_reading_plan_commands_write_only_placeholder_contracts(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    create = runner.invoke(
        app,
        [
            "reading-plan",
            "create",
            str(tmp_path),
            "deep-work",
            "--plan-id",
            "daily-ddia",
            "--daily-sections",
            "2",
        ],
    )
    next_result = runner.invoke(app, ["reading-plan", "next", str(tmp_path), "daily-ddia"])
    mark = runner.invoke(
        app,
        [
            "reading-plan",
            "mark-read",
            str(tmp_path),
            "daily-ddia",
            "--section-id",
            "deep-work.intro",
        ],
    )

    assert create.exit_code == 0, create.output
    assert next_result.exit_code == 0, next_result.output
    assert mark.exit_code == 0, mark.output
    assert _placeholder(tmp_path, "reading-plan-create-daily-ddia")["daily_sections"] == 2
    assert _placeholder(tmp_path, "reading-plan-next-daily-ddia")["backend_not_run"] is True
    mark_payload = _placeholder(tmp_path, "reading-plan-mark-read-daily-ddia")
    assert mark_payload["section_id"] == "deep-work.intro"
    assert not (tmp_path / "reading_plans" / "daily-ddia.json").exists()


def test_placeholder_commands_support_dry_run_without_writing(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["segment", str(tmp_path), "deep-work", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Backend not run" in result.output
    assert not (tmp_path / "runs" / "cli-placeholders" / "segment-deep-work.json").exists()


def test_placeholder_commands_validate_ids(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["segment", str(tmp_path), "../../../escape"])

    assert result.exit_code != 0
    assert "doc_id must be a lowercase hyphenated slug" in result.output
