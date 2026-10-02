from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.annotations import annotation_path, build_annotation, write_annotation
from bookgraph.cli import app
from bookgraph.concept_registry import read_registry
from bookgraph.index.sqlite import SqliteIndexBackend
from bookgraph.mcp import service
from bookgraph.mcp.service import ReadingServiceError
from bookgraph.models import AnnotatedConcept, Section
from bookgraph.sections import write_sections
from bookgraph.workspace import WorkspacePaths

runner = CliRunner()


def _section(section_id: str, title: str) -> Section:
    return Section(
        id=section_id, doc_id="iceberg", title=title, level=1, heading_path=[title], text="x"
    )


def _annotate(workspace: WorkspacePaths, section_id: str, *titles: str) -> None:
    annotation = build_annotation(
        "iceberg",
        section_id,
        [AnnotatedConcept(slug="", title=title, gloss=f"{title} here") for title in titles],
    )
    write_annotation(annotation, annotation_path(workspace.annotations_root, "iceberg", section_id))


def _workspace(tmp_path: Path) -> WorkspacePaths:
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    workspace = WorkspacePaths(tmp_path)
    write_sections(
        [_section("iceberg.a", "Metadata"), _section("iceberg.b", "Commits")],
        workspace.sources_sections / "iceberg",
    )
    _annotate(workspace, "iceberg.a", "Table Metadata", "Snapshots")
    _annotate(workspace, "iceberg.b", "Metadata File", "Snapshot")
    return workspace


def _invoke(*args: str) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_alias_folds_mentions_into_the_canonical_after_rebuild(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    ws = str(tmp_path)
    _invoke("index", "build", ws)

    suggested = _invoke("concepts", "suggest", ws)
    assert "snapshots -> snapshot" in suggested
    review = _invoke("concepts", "review", ws)
    assert "metadata-file" in review and "pending: 4" in review

    _invoke("concepts", "alias", ws, "metadata-file", "table-metadata")
    record = read_registry(workspace.concept_registry).record("table-metadata")
    assert record is not None and record.title == "Table Metadata"

    # Before the rebuild the alias's mentions still sit under its own slug: the
    # canonical view folds them in at read time, and lint flags the alias node as stale.
    early = service.get_concept(workspace, "metadata-file")
    assert (early.slug, early.mention_count, early.resolved_from) == (
        "table-metadata",
        2,
        "metadata-file",
    )
    assert [m.raw_slug for m in early.mentions] == ["", "metadata-file"]
    assert "stale-alias" in _invoke("concepts", "lint", ws)

    _invoke("index", "build", ws)
    concept = service.get_concept(workspace, "metadata-file")

    assert concept.slug == "table-metadata"
    assert concept.title == "Table Metadata"
    assert concept.canonical is True
    assert concept.resolved_from == "metadata-file"
    assert concept.aliases == ["metadata-file"]
    assert concept.mention_count == 2
    assert [(m.section_id, m.raw_slug, m.gloss) for m in concept.mentions] == [
        ("iceberg.a", "", "Table Metadata here"),
        ("iceberg.b", "metadata-file", "Metadata File here"),
    ]
    review = _invoke("concepts", "review", ws)
    assert "metadata-file" not in review and "table-metadata" not in review


def test_hygiene_report_and_concept_pages_reflect_the_registry(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    ws = str(tmp_path)
    _invoke("concepts", "alias", ws, "snapshots", "snapshot", "--title", "Snapshot")
    _invoke("concepts", "ignore", ws, "metadata-file")
    _invoke("index", "build", ws)

    report = service.concept_hygiene(workspace)
    assert report.concept_count == 2
    assert {item.slug for item in report.review_queue} == {"table-metadata"}
    assert report.merge_suggestions == []

    _invoke("index", "concepts", ws)
    page = (workspace.wiki_concepts / "snapshot.md").read_text()
    assert page.startswith("# Snapshot\n")
    assert "Also known as: `snapshots`." in page
    assert not (workspace.wiki_concepts / "metadata-file.md").exists()


def test_index_concepts_durable_only_skips_lint_warnings(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    write_sections(
        [_section("iceberg.a", "However"), _section("iceberg.b", "Snapshot")],
        workspace.sources_sections / "iceberg",
    )
    _annotate(workspace, "iceberg.b", "Snapshot")
    SqliteIndexBackend().build_document(
        workspace,
        "iceberg",
        "Iceberg",
        [_section("iceberg.a", "However"), _section("iceberg.b", "Snapshot")],
    )

    output = _invoke("index", "concepts", str(tmp_path), "--durable-only")

    assert (workspace.wiki_concepts / "snapshot.md").is_file()
    assert not (workspace.wiki_concepts / "however.md").exists()
    assert "skipped: 1" in output


def test_invalid_registry_aborts_index_build(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.concept_registry.parent.mkdir(parents=True, exist_ok=True)
    workspace.concept_registry.write_text("{not json")

    result = runner.invoke(app, ["index", "build", str(tmp_path)])

    assert result.exit_code != 0
    assert "Invalid concept registry" in result.output


def test_get_concept_serves_a_canonical_that_has_no_rows_yet(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    ws = str(tmp_path)
    _invoke("index", "build", ws)
    _invoke("concepts", "alias", ws, "snapshots", "point-in-time-state", "--title", "State")

    for requested in ("snapshots", "point-in-time-state"):
        concept = service.get_concept(workspace, requested)

        assert concept.slug == "point-in-time-state"
        assert concept.title == "State"
        assert concept.canonical is True
        assert concept.aliases == ["snapshots"]
        assert [(m.section_id, m.raw_slug) for m in concept.mentions] == [
            ("iceberg.a", "snapshots")
        ]
    assert service.get_concept(workspace, "snapshots").resolved_from == "snapshots"
    assert service.get_concept(workspace, "point-in-time-state").resolved_from is None


def test_concept_hygiene_validates_arguments_and_caps_each_list(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _invoke("index", "build", str(tmp_path))

    with pytest.raises(ReadingServiceError, match="limit"):
        service.concept_hygiene(workspace, limit=0)
    for threshold in (-0.1, 1.1):
        with pytest.raises(ReadingServiceError, match="threshold"):
            service.concept_hygiene(workspace, threshold=threshold)

    full = service.concept_hygiene(workspace, threshold=0.0)
    capped = service.concept_hygiene(workspace, limit=1, threshold=0.0)
    assert len(full.review_queue) == 4
    assert len(capped.review_queue) == 1
    assert len(capped.merge_suggestions) <= 1
    assert len(capped.lint) <= 1
    assert capped.concept_count == full.concept_count == 4


def test_alias_command_rejects_alias_chains(tmp_path: Path) -> None:
    _workspace(tmp_path)
    ws = str(tmp_path)
    _invoke("concepts", "alias", ws, "metadata-file", "table-metadata")

    result = runner.invoke(app, ["concepts", "alias", ws, "catalog-pointer", "metadata-file"])

    assert result.exit_code != 0
    assert "alias of 'table-metadata'" in result.output
