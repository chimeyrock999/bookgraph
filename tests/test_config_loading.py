from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app
from bookgraph.cli._config import load_config
from bookgraph.workspace import WorkspacePaths


def _init_workspace(tmp_path: Path) -> CliRunner:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return runner


def _write_document(workspace: Path, doc_id: str) -> None:
    parsed_dir = workspace / "sources" / "parsed" / doc_id
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / "document.json").write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "title": "Deep Work",
                "blocks": [
                    {"id": "b1", "type": "title", "text": "Part 1", "level": 1, "order": 0},
                    {"id": "b2", "type": "title", "text": "Chapter 1", "level": 2, "order": 1},
                    {"id": "b3", "type": "text", "text": "Body.", "order": 2},
                ],
                "metadata": {"parser": "markdown"},
            }
        )
    )


def _write_sections_manifest(workspace: Path, doc_id: str, section_ids: list[str]) -> None:
    sections_dir = workspace / "sources" / "sections" / doc_id
    sections_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for section_id in section_ids:
        lines.append(
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
        )
    (sections_dir / "sections.jsonl").write_text("\n".join(lines) + "\n")


def test_load_config_reads_segmenter_and_reading_plan_defaults(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    (tmp_path / "bookgraph.toml").write_text(
        """
[segmenter]
default = "heading"
target_level = 1

[reading_plan]
daily_sections = 3
""".strip()
        + "\n"
    )

    config = load_config(WorkspacePaths(tmp_path))

    assert config.segmenter.default == "heading"
    assert config.segmenter.target_level == 1
    assert config.reading_plan.daily_sections == 3


def test_segment_uses_configured_target_level(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    (tmp_path / "bookgraph.toml").write_text(
        """
[segmenter]
default = "heading"
target_level = 1
""".strip()
        + "\n"
    )
    _write_document(tmp_path, "deep-work")

    result = runner.invoke(app, ["segment", str(tmp_path), "deep-work"])

    assert result.exit_code == 0, result.output
    assert "segmenter: heading" in result.output
    assert "target_level: 1" in result.output
    manifest = tmp_path / "sources" / "sections" / "deep-work" / "sections.jsonl"
    lines = manifest.read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["deep-work.part-1"]


def test_reading_plan_create_uses_configured_daily_sections(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    (tmp_path / "bookgraph.toml").write_text(
        """
[reading_plan]
daily_sections = 2
""".strip()
        + "\n"
    )
    _write_sections_manifest(tmp_path, "deep-work", ["deep-work.a", "deep-work.b", "deep-work.c"])

    result = runner.invoke(app, ["reading-plan", "create", str(tmp_path), "deep-work"])

    assert result.exit_code == 0, result.output
    assert "daily_sections: 2" in result.output
    plan = json.loads((tmp_path / "reading_plans" / "deep-work.json").read_text())
    assert plan["daily_sections"] == 2


def test_reading_plan_cli_flag_overrides_configured_daily_sections(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    (tmp_path / "bookgraph.toml").write_text("[reading_plan]\ndaily_sections = 2\n")
    _write_sections_manifest(tmp_path, "deep-work", ["deep-work.a", "deep-work.b", "deep-work.c"])

    result = runner.invoke(
        app,
        ["reading-plan", "create", str(tmp_path), "deep-work", "--daily-sections", "1"],
    )

    assert result.exit_code == 0, result.output
    assert "daily_sections: 1" in result.output
    plan = json.loads((tmp_path / "reading_plans" / "deep-work.json").read_text())
    assert plan["daily_sections"] == 1
