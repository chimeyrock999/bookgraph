from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app
from bookgraph.indexes import read_index
from bookgraph.models import Section
from bookgraph.sections import write_sections
from bookgraph.workspace import WorkspacePaths

runner = CliRunner()


def _section(section_id: str, doc_id: str, title: str, text: str) -> Section:
    return Section(
        id=section_id,
        doc_id=doc_id,
        title=title,
        level=1,
        heading_path=[title],
        text=text,
    )


def _init_workspace(tmp_path: Path) -> WorkspacePaths:
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return WorkspacePaths(tmp_path)


def test_index_build_writes_index_for_one_document(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    write_sections(
        [_section("deep-work.a", "deep-work", "Storage", "storage storage index")],
        workspace.sources_sections / "deep-work",
    )

    result = runner.invoke(app, ["index", "build", str(tmp_path), "--doc-id", "deep-work"])

    assert result.exit_code == 0, result.output
    index = read_index(workspace.indexes_root / "sections" / "deep-work.json")
    assert index.doc_id == "deep-work"
    assert index.postings["storage"] == {"deep-work.a": 3}


def test_index_build_indexes_every_segmented_document(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    write_sections(
        [_section("deep-work.a", "deep-work", "Storage", "storage text")],
        workspace.sources_sections / "deep-work",
    )
    write_sections(
        [_section("ddia.x", "ddia", "Replication", "leaders and followers")],
        workspace.sources_sections / "ddia",
    )

    result = runner.invoke(app, ["index", "build", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (workspace.indexes_root / "sections" / "deep-work.json").is_file()
    assert (workspace.indexes_root / "sections" / "ddia.json").is_file()


def test_index_build_errors_when_nothing_is_segmented(tmp_path: Path) -> None:
    _init_workspace(tmp_path)

    result = runner.invoke(app, ["index", "build", str(tmp_path)])

    assert result.exit_code != 0
    assert "No segmented documents" in result.output


def test_index_build_rejects_traversal_doc_id(tmp_path: Path) -> None:
    _init_workspace(tmp_path)

    result = runner.invoke(app, ["index", "build", str(tmp_path), "--doc-id", "../escape"])

    assert result.exit_code != 0
