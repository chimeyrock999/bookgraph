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


def test_llmwiki_serve_uncompiled_project(tmp_path: Path) -> None:
    # Staging alone is not enough: without a compile the llmwiki project has no
    # state, so serving must fail cleanly instead of serving an empty project.
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")
    assert runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work"]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path)])

    assert result.exit_code != 0
    assert "No compiled llmwiki project" in result.output


def test_llmwiki_serve_print_emits_root_contract(tmp_path: Path) -> None:
    # --print is pure command generation and must not require a compiled project.
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "serve", str(tmp_path), "--print"])

    assert result.exit_code == 0, result.output
    project = (tmp_path / "llmwiki").resolve()
    # Real llm-wiki-compiler v1.1 contract: `serve --root <project>`, where the
    # project is the isolated llmwiki/ subtree, not the workspace root or wiki/.
    assert result.output.strip() == f"llmwiki serve --root {project}"


def test_llmwiki_serve_print_quotes_spaced_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "my workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "serve", str(workspace), "--print"])

    assert result.exit_code == 0, result.output
    project = (workspace / "llmwiki").resolve()
    assert result.output.strip() == f"llmwiki serve --root '{project}'"


def test_llmwiki_bridge_stages_sections_with_provenance(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    result = runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work"])

    assert result.exit_code == 0, result.output
    assert "staged: 2" in result.output
    assert "unchanged: 0" in result.output

    intro = tmp_path / "llmwiki" / "sources" / "deep-work.intro.md"
    assert intro.is_file()
    text = intro.read_text()
    assert 'bookgraph_doc_id: "deep-work"' in text
    assert 'bookgraph_section_id: "deep-work.intro"' in text
    assert "# Intro" in text
    assert "Hello wiki." in text
    # BookGraph's canonical section markdown must not be touched — staged llmwiki
    # sources live in the isolated llmwiki/ subtree, not the sources/sections/ tree.
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
    assert (tmp_path / "llmwiki" / "sources" / "deep-work.intro.md").is_file()
    assert not (tmp_path / "llmwiki" / "sources" / "deep-work.chapter-1.md").exists()


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
    assert not (tmp_path / "llmwiki").exists()


def test_llmwiki_bridge_compile_print_emits_root_contract(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    result = runner.invoke(
        app, ["llmwiki", "bridge", str(tmp_path), "deep-work", "--compile", "--print"]
    )

    assert result.exit_code == 0, result.output
    project = (tmp_path / "llmwiki").resolve()
    assert f"llmwiki compile --root {project}" in result.output


def test_llmwiki_bridge_print_without_compile_errors(tmp_path: Path) -> None:
    # --print only means anything for the compile step; without --compile it must
    # not silently no-op.
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")

    result = runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work", "--print"])

    assert result.exit_code != 0
    assert "--print applies to the compile step" in result.output
    # Rejected before any staging happened.
    assert not (tmp_path / "llmwiki").exists()


def test_llmwiki_bridge_missing_sections(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "missing"])

    assert result.exit_code != 0
    assert "Sections manifest not found" in result.output


def test_llmwiki_serve_guard_passes_once_compiled_print(tmp_path: Path) -> None:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    _write_sections_manifest(tmp_path, "deep-work")
    assert runner.invoke(app, ["llmwiki", "bridge", str(tmp_path), "deep-work"]).exit_code == 0
    # Simulate a completed llmwiki compile by writing its state marker.
    state = tmp_path / "llmwiki" / ".llmwiki" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}\n")

    # --print never needs the guard; but confirm the compiled project also serves.
    assert runner.invoke(app, ["llmwiki", "serve", str(tmp_path), "--print"]).exit_code == 0
