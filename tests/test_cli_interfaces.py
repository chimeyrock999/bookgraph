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


def test_parse_book_dry_run_writes_placeholder_contract(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(
        app,
        [
            "parse-book",
            str(tmp_path),
            "deep-work",
            "--runner",
            "mineru",
            "--method",
            "ocr",
            "--backend",
            "pipeline",
            "--timeout-seconds",
            "7200",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _placeholder(tmp_path, "parse-book-deep-work")
    assert payload["command"] == "parse-book"
    assert payload["book_id"] == "deep-work"
    assert payload["runner"] == {
        "name": "mineru",
        "command": "mineru",
        "method": "ocr",
        "backend": "pipeline",
        "timeout_seconds": 7200,
    }
    assert payload["parser"] == "mineru-middle-json"
    assert payload["inputs"] == {
        "book_manifest": str(tmp_path / "sources" / "inbox" / "deep-work" / "book.json"),
        "original_source": str(tmp_path / "sources" / "inbox" / "deep-work" / "original.pdf"),
    }
    assert payload["intermediate_outputs"] == {
        "parsed_dir": str(tmp_path / "sources" / "parsed" / "deep-work"),
        "middle_json": str(
            tmp_path / "sources" / "parsed" / "deep-work" / "deep-work_middle.json"
        ),
        "markdown": str(tmp_path / "sources" / "parsed" / "deep-work" / "deep-work.md"),
        "layout_pdf": str(
            tmp_path / "sources" / "parsed" / "deep-work" / "deep-work_layout.pdf"
        ),
        "span_pdf": str(
            tmp_path / "sources" / "parsed" / "deep-work" / "deep-work_span.pdf"
        ),
        "content_list": str(
            tmp_path / "sources" / "parsed" / "deep-work" / "deep-work_content_list.json"
        ),
        "images_dir": str(tmp_path / "sources" / "parsed" / "deep-work" / "images"),
    }
    assert payload["backend_not_run"] is True
    assert not (tmp_path / "sources" / "parsed" / "deep-work").exists()
    assert "Backend not run" in result.output


def test_parse_book_uses_manifest_source_type_for_original_source(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    manifest = tmp_path / "sources" / "inbox" / "deep-work" / "book.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"source_type": "epub"}))

    result = runner.invoke(app, ["parse-book", str(tmp_path), "deep-work", "--dry-run"])

    assert result.exit_code == 0, result.output
    payload = _placeholder(tmp_path, "parse-book-deep-work")
    assert payload["inputs"] == {
        "book_manifest": str(manifest),
        "original_source": str(manifest.parent / "original.epub"),
    }


def _write_parsed_document(tmp_path: Path, doc_id: str) -> None:
    parsed_dir = tmp_path / "sources" / "parsed" / doc_id
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / "document.json").write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "title": "Deep Work",
                "blocks": [
                    {"id": "b1", "type": "title", "text": "Chapter 1", "level": 1, "order": 0},
                    {"id": "b2", "type": "text", "text": "Body.", "order": 1},
                ],
                "metadata": {"parser": "markdown"},
            }
        )
    )


def test_segment_writes_sections_from_parsed_document(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_parsed_document(tmp_path, "deep-work")

    result = runner.invoke(app, ["segment", str(tmp_path), "deep-work", "--segmenter", "heading"])

    assert result.exit_code == 0, result.output
    sections_dir = tmp_path / "sources" / "sections" / "deep-work"
    manifest = sections_dir / "sections.jsonl"
    assert manifest.is_file()
    lines = manifest.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == "deep-work.chapter-1"
    assert (sections_dir / "deep-work.chapter-1.md").is_file()
    assert "segmenter: heading" in result.output
    assert "sections: 1" in result.output
    # A real segment run never writes a placeholder request artifact.
    assert not (tmp_path / "runs" / "cli-placeholders" / "segment-deep-work.json").exists()


def test_segment_reports_a_missing_parsed_document(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["segment", str(tmp_path), "deep-work"])

    assert result.exit_code != 0
    assert "Parsed document not found" in result.output
    assert not (tmp_path / "sources" / "sections" / "deep-work").exists()


def test_segment_reports_a_corrupt_parsed_document(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    parsed_dir = tmp_path / "sources" / "parsed" / "deep-work"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / "document.json").write_text("not json")

    result = runner.invoke(app, ["segment", str(tmp_path), "deep-work"])

    assert result.exit_code != 0
    assert "Invalid parsed document" in result.output
    assert not (tmp_path / "sources" / "sections" / "deep-work").exists()


def test_wiki_compile_dry_run_writes_placeholder_contract(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["wiki", "compile", str(tmp_path), "deep-work", "--dry-run"])

    assert result.exit_code == 0, result.output
    payload = _placeholder(tmp_path, "wiki-compile-deep-work")
    assert payload["command"] == "wiki compile"
    assert payload["backend"] == "llmwiki"
    assert payload["backend_not_run"] is True
    assert not (tmp_path / "wiki" / "books" / "deep-work").exists()


def _write_sections_manifest(tmp_path: Path, doc_id: str, section_ids: list[str]) -> Path:
    sections_dir = tmp_path / "sources" / "sections" / doc_id
    sections_dir.mkdir(parents=True, exist_ok=True)
    manifest = sections_dir / "sections.jsonl"
    lines = [
        json.dumps(
            {
                "id": section_id,
                "doc_id": doc_id,
                "title": section_id,
                "level": 1,
                "heading_path": [section_id],
                "text": "Body.",
                "block_ids": [],
            }
        )
        for section_id in section_ids
    ]
    manifest.write_text("\n".join(lines) + "\n")
    return manifest


def test_reading_plan_create_builds_plan_from_sections(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_sections_manifest(tmp_path, "deep-work", ["deep-work.a", "deep-work.b", "deep-work.c"])

    result = runner.invoke(
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

    assert result.exit_code == 0, result.output
    assert "sections: 3" in result.output
    plan = json.loads((tmp_path / "reading_plans" / "daily-ddia.json").read_text())
    assert plan["plan_id"] == "daily-ddia"
    assert plan["doc_id"] == "deep-work"
    assert plan["daily_sections"] == 2
    assert plan["section_ids"] == ["deep-work.a", "deep-work.b", "deep-work.c"]
    assert plan["completed"] == []


def test_reading_plan_create_reports_a_missing_sections_manifest(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["reading-plan", "create", str(tmp_path), "deep-work"])

    assert result.exit_code != 0
    assert "Sections manifest not found" in result.output
    assert not (tmp_path / "reading_plans" / "deep-work.json").exists()


def test_reading_plan_create_dry_run_writes_nothing(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_sections_manifest(tmp_path, "deep-work", ["deep-work.a"])

    result = runner.invoke(
        app, ["reading-plan", "create", str(tmp_path), "deep-work", "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "sections: 1" in result.output
    assert not (tmp_path / "reading_plans" / "deep-work.json").exists()


def test_reading_plan_next_returns_the_daily_batch(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_sections_manifest(tmp_path, "deep-work", ["deep-work.a", "deep-work.b", "deep-work.c"])
    create = runner.invoke(
        app,
        ["reading-plan", "create", str(tmp_path), "deep-work", "--daily-sections", "2"],
    )
    assert create.exit_code == 0, create.output

    result = runner.invoke(app, ["reading-plan", "next", str(tmp_path), "deep-work"])

    assert result.exit_code == 0, result.output
    assert "next: deep-work.a, deep-work.b" in result.output
    assert "remaining: 3" in result.output


def test_reading_plan_next_reports_a_missing_plan(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["reading-plan", "next", str(tmp_path), "missing"])

    assert result.exit_code != 0
    assert "Reading plan not found" in result.output


def test_reading_plan_mark_read_advances_progress(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_sections_manifest(tmp_path, "deep-work", ["deep-work.a", "deep-work.b"])
    runner.invoke(app, ["reading-plan", "create", str(tmp_path), "deep-work"])

    # Default marks the next unread section.
    first = runner.invoke(app, ["reading-plan", "mark-read", str(tmp_path), "deep-work"])
    assert first.exit_code == 0, first.output
    assert "marked: deep-work.a" in first.output
    assert "completed: 1/2" in first.output

    # An explicit section id marks that one.
    second = runner.invoke(
        app,
        ["reading-plan", "mark-read", str(tmp_path), "deep-work", "--section-id", "deep-work.b"],
    )
    assert second.exit_code == 0, second.output
    assert "completed: 2/2" in second.output
    plan = json.loads((tmp_path / "reading_plans" / "deep-work.json").read_text())
    assert plan["completed"] == ["deep-work.a", "deep-work.b"]

    # After completion, next reports nothing left.
    nxt = runner.invoke(app, ["reading-plan", "next", str(tmp_path), "deep-work"])
    assert "next: (complete)" in nxt.output
    assert "remaining: 0" in nxt.output


def test_reading_plan_mark_read_rejects_an_unknown_section(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_sections_manifest(tmp_path, "deep-work", ["deep-work.a"])
    runner.invoke(app, ["reading-plan", "create", str(tmp_path), "deep-work"])

    result = runner.invoke(
        app,
        [
            "reading-plan",
            "mark-read",
            str(tmp_path),
            "deep-work",
            "--section-id",
            "deep-work.ghost",
        ],
    )

    assert result.exit_code != 0
    assert "is not in reading plan" in result.output


def test_placeholder_commands_support_dry_run_without_writing(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["wiki", "compile", str(tmp_path), "deep-work", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Backend not run" in result.output
    assert (tmp_path / "runs" / "cli-placeholders" / "wiki-compile-deep-work.json").exists()


def test_placeholder_commands_validate_ids(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(app, ["wiki", "compile", str(tmp_path), "../../../escape"])

    assert result.exit_code != 0
    assert "doc_id must be a lowercase hyphenated slug" in result.output


def test_parse_book_rejects_negative_timeout(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(
        app,
        ["parse-book", str(tmp_path), "deep-work", "--timeout-seconds", "-1"],
    )

    assert result.exit_code != 0
    assert "timeout_seconds must be non-negative" in result.output


def test_placeholder_commands_validate_plugin_names(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    parse = runner.invoke(
        app,
        ["parse-book", str(tmp_path), "deep-work", "--parser", "mineru-middlejson"],
    )
    segment = runner.invoke(
        app,
        ["segment", str(tmp_path), "deep-work", "--segmenter", "headng"],
    )
    wiki = runner.invoke(
        app,
        ["wiki", "compile", str(tmp_path), "deep-work", "--backend", "missing"],
    )

    assert parse.exit_code != 0
    assert "Unknown parser plugin" in parse.output
    assert "mineru-middle-json" in parse.output
    assert segment.exit_code != 0
    assert "Unknown segmenter plugin" in segment.output
    assert "heading" in segment.output
    assert wiki.exit_code != 0
    assert "Unknown wiki backend plugin" in wiki.output
    assert "llmwiki" in wiki.output


def test_reading_plan_create_rejects_non_positive_daily_sections(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(
        app,
        ["reading-plan", "create", str(tmp_path), "deep-work", "--daily-sections", "0"],
    )

    assert result.exit_code != 0
    assert "daily_sections must be at least 1" in result.output
