from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app

runner = CliRunner()


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


def test_llmwiki_serve_is_registered() -> None:
    result = runner.invoke(app, ["llmwiki", "--help"])

    assert result.exit_code == 0, result.output
    assert "serve" in result.output
    assert "bridge" in result.output


def test_llmwiki_serve_missing_workspace(tmp_path: Path) -> None:
    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path / "nope")])

    assert result.exit_code != 0
    assert "Workspace not found" in result.output


def test_llmwiki_serve_uncompiled_wiki(tmp_path: Path) -> None:
    # Realistic flow: init creates the sources/ skeleton but no section has been
    # bridged into an llmwiki source yet, so serving must fail cleanly instead of
    # serving an empty llmwiki project.
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path)])

    assert result.exit_code != 0
    assert "No llmwiki sources staged" in result.output


def test_llmwiki_serve_print_emits_root_contract(tmp_path: Path) -> None:
    # --print is pure command generation and must not require a staged project.
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path), "--print"])

    assert result.exit_code == 0, result.output
    workspace = tmp_path.resolve()
    # Real llm-wiki-compiler v1.1 contract: `serve --root <project>`, not a
    # positional root, and not the workspace's wiki/ subdirectory.
    assert result.output.strip() == f"llmwiki serve --root {workspace}"


def test_llmwiki_serve_print_quotes_spaced_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "my workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "serve", str(workspace), "--print"])

    assert result.exit_code == 0, result.output
    resolved = workspace.resolve()
    assert result.output.strip() == f"llmwiki serve --root '{resolved}'"


def test_llmwiki_bridge_stages_sections_with_provenance(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    result = runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work"])

    assert result.exit_code == 0, result.output
    assert "staged: 2" in result.output
    assert "unchanged: 0" in result.output

    intro = tmp_path / "sources" / "deep-work.intro.md"
    assert intro.is_file()
    text = intro.read_text()
    assert 'bookgraph_doc_id: "deep-work"' in text
    assert 'bookgraph_section_id: "deep-work.intro"' in text
    assert "# Intro" in text
    assert "Hello wiki." in text
    # BookGraph's canonical section markdown must not be touched — staged llmwiki
    # sources are top-level sources/*.md, not the sources/sections/ tree.
    assert (tmp_path / "sources" / "sections" / "deep-work" / "sections.jsonl").is_file()


def test_llmwiki_bridge_is_idempotent(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    assert runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work"]).exit_code == 0
    result = runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work"])

    assert result.exit_code == 0, result.output
    assert "staged: 0" in result.output
    assert "unchanged: 2" in result.output


def test_llmwiki_bridge_plan_stages_only_read_sections(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")
    assert (
        runner.invoke(
            app, ["reading-plan", "create", str(tmp_path), "deep-work"]
        ).exit_code
        == 0
    )
    # Read the first section only.
    mark = runner.invoke(app, ["reading-plan", "mark-read", str(tmp_path), "deep-work"])
    assert mark.exit_code == 0, mark.output

    result = runner.invoke(
        app, ["llmwiki", "bridge", str(tmp_path), "deep-work", "--plan", "deep-work"]
    )

    assert result.exit_code == 0, result.output
    assert "staged: 1" in result.output
    assert (tmp_path / "sources" / "deep-work.intro.md").is_file()
    assert not (tmp_path / "sources" / "deep-work.chapter-1.md").exists()


def test_llmwiki_bridge_plan_with_nothing_read_stages_nothing(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")
    assert (
        runner.invoke(
            app, ["reading-plan", "create", str(tmp_path), "deep-work"]
        ).exit_code
        == 0
    )

    result = runner.invoke(
        app, ["llmwiki", "bridge", str(tmp_path), "deep-work", "--plan", "deep-work"]
    )

    assert result.exit_code == 0, result.output
    assert "nothing to stage" in result.output
    assert not any((tmp_path / "sources").glob("*.md"))


def test_llmwiki_bridge_compile_print_emits_root_contract(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    result = runner.invoke(
        app, ["llmwiki", "bridge", str(tmp_path), "deep-work", "--compile", "--print"]
    )

    assert result.exit_code == 0, result.output
    workspace = tmp_path.resolve()
    assert f"llmwiki compile --root {workspace}" in result.output


def test_llmwiki_bridge_missing_sections(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "missing"])

    assert result.exit_code != 0
    assert "Sections manifest not found" in result.output


def test_llmwiki_serve_after_bridge_passes_guard_print(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")
    assert runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work"]).exit_code == 0

    # After bridging, a staged source exists so the serve guard is satisfied; use
    # --print so the test does not require llmwiki on PATH.
    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path), "--print"])

    assert result.exit_code == 0, result.output
    assert "--root" in result.output
