from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookgraph.index.sqlite import SqliteIndexBackend, db_path
from bookgraph.mcp import service
from bookgraph.mcp.service import (
    InvalidIdError,
    PlanNotFoundError,
    ReadingServiceError,
    SectionNotFoundError,
    SectionsNotFoundError,
)
from bookgraph.models import ReadingPlan, Section
from bookgraph.reading_plans import write_reading_plan
from bookgraph.sections import read_sections, write_sections
from bookgraph.workspace import WorkspacePaths


def _build_index(workspace: WorkspacePaths, doc_id: str) -> None:
    """Index a segmented document exactly as ``bookgraph index build`` would."""

    sections = read_sections(workspace.sources_sections / doc_id / "sections.jsonl")
    SqliteIndexBackend().build_document(workspace, doc_id, doc_id, sections)


def _section(section_id: str, title: str, text: str = "Body.") -> Section:
    return Section(
        id=section_id,
        doc_id="deep-work",
        title=title,
        level=1,
        heading_path=[title],
        text=text,
    )


def _workspace(tmp_path: Path, *sections: Section) -> WorkspacePaths:
    workspace = WorkspacePaths(tmp_path)
    if sections:
        write_sections(list(sections), workspace.sources_sections / "deep-work")
    return workspace


def _plan(workspace: WorkspacePaths, *section_ids: str, completed: list[str] | None = None) -> None:
    plan = ReadingPlan(
        plan_id="daily",
        doc_id="deep-work",
        daily_sections=2,
        section_ids=list(section_ids),
        completed=completed or [],
    )
    write_reading_plan(plan, workspace.reading_plans_root / "daily.json")


def test_get_next_section_returns_unread_batch_with_content(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        _section("deep-work.a", "Alpha", "Alpha body."),
        _section("deep-work.b", "Beta", "Beta body."),
        _section("deep-work.c", "Gamma", "Gamma body."),
    )
    _plan(workspace, "deep-work.a", "deep-work.b", "deep-work.c", completed=["deep-work.a"])

    result = service.get_next_section(workspace, "daily")

    assert [view.id for view in result.sections] == ["deep-work.b", "deep-work.c"]
    assert result.sections[0].text == "Beta body."
    assert result.sections[0].markdown_path == str(
        workspace.sources_sections / "deep-work" / "deep-work.b.md"
    )
    assert result.remaining == 2
    assert result.done is False


def test_get_next_section_reports_done_when_all_read(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", "Alpha"))
    _plan(workspace, "deep-work.a", completed=["deep-work.a"])

    result = service.get_next_section(workspace, "daily")

    assert result.sections == []
    assert result.remaining == 0
    assert result.done is True


def test_get_next_section_raises_for_missing_plan(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", "Alpha"))

    with pytest.raises(PlanNotFoundError, match="not found"):
        service.get_next_section(workspace, "ghost")


def test_get_next_section_raises_when_plan_points_at_unknown_section(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", "Alpha"))
    _plan(workspace, "deep-work.a", "deep-work.ghost")

    with pytest.raises(SectionNotFoundError, match="unknown section"):
        service.get_next_section(workspace, "daily")


def test_get_section_returns_one_section(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        _section("deep-work.a", "Alpha"),
        _section("deep-work.b", "Beta", "Beta body."),
    )

    view = service.get_section(workspace, "deep-work", "deep-work.b")

    assert view.title == "Beta"
    assert view.text == "Beta body."


def test_get_section_raises_for_unknown_section(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", "Alpha"))

    with pytest.raises(SectionNotFoundError, match="not found"):
        service.get_section(workspace, "deep-work", "deep-work.ghost")


def test_get_section_raises_for_unsegmented_document(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)

    with pytest.raises(SectionsNotFoundError, match="No sections"):
        service.get_section(workspace, "deep-work", "deep-work.a")


def test_mark_read_advances_and_persists(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path, _section("deep-work.a", "Alpha"), _section("deep-work.b", "Beta")
    )
    _plan(workspace, "deep-work.a", "deep-work.b")

    first = service.mark_read(workspace, "daily")
    assert first.marked == "deep-work.a"
    assert (first.completed, first.total, first.done) == (1, 2, False)

    second = service.mark_read(workspace, "daily", "deep-work.b")
    assert second.done is True

    persisted = json.loads((workspace.reading_plans_root / "daily.json").read_text())
    assert persisted["completed"] == ["deep-work.a", "deep-work.b"]


def test_mark_read_raises_for_unknown_section(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", "Alpha"))
    _plan(workspace, "deep-work.a")

    with pytest.raises(ReadingServiceError, match="not in reading plan"):
        service.mark_read(workspace, "daily", "deep-work.ghost")


def test_search_scan_fallback_ranks_by_term_frequency(tmp_path: Path) -> None:
    # No index built: search scores via the live-scan term-frequency fallback.
    workspace = _workspace(
        tmp_path,
        _section("deep-work.a", "Storage engines", "storage storage storage index"),
        _section("deep-work.b", "Replication", "leaders and followers"),
        _section("deep-work.c", "Indexes", "an index on storage"),
    )
    assert not db_path(workspace).exists()

    result = service.search_sections(workspace, "storage")

    assert [hit.section_id for hit in result.hits] == ["deep-work.a", "deep-work.c"]
    assert result.hits[0].score == 4  # title + 3 in text
    assert "storage" in result.hits[0].snippet.lower()


def test_search_can_scope_to_a_single_document(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        _section("deep-work.a", "Storage", "storage text"),
    )
    # A second document that also matches.
    write_sections(
        [
            Section(
                id="ddia.x",
                doc_id="ddia",
                title="Storage",
                level=1,
                heading_path=["Storage"],
                text="storage text",
            )
        ],
        workspace.sources_sections / "ddia",
    )

    scoped = service.search_sections(workspace, "storage", doc_id="deep-work")
    assert [hit.doc_id for hit in scoped.hits] == ["deep-work"]

    everything = service.search_sections(workspace, "storage")
    assert sorted(hit.doc_id for hit in everything.hits) == ["ddia", "deep-work"]


def test_search_respects_limit(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        _section("deep-work.a", "One", "match"),
        _section("deep-work.b", "Two", "match match"),
        _section("deep-work.c", "Three", "match match match"),
    )

    result = service.search_sections(workspace, "match", limit=2)

    assert [hit.section_id for hit in result.hits] == ["deep-work.c", "deep-work.b"]


def test_search_uses_persisted_index_when_present(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        _section("deep-work.a", "Storage engines", "storage storage storage index"),
        _section("deep-work.c", "Indexes", "an index on storage"),
    )
    _build_index(workspace, "deep-work")
    assert db_path(workspace).is_file()

    result = service.search_sections(workspace, "storage")

    # FTS5 bm25 ranks the storage-heavy section first, same order as the scan.
    assert [hit.section_id for hit in result.hits] == ["deep-work.a", "deep-work.c"]
    assert result.hits[0].score > result.hits[1].score
    assert "storage" in result.hits[0].snippet.lower()


def test_search_falls_back_to_scan_for_a_corrupt_index(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        _section("deep-work.a", "Storage", "storage text"),
    )
    path = db_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a sqlite database")

    result = service.search_sections(workspace, "storage")

    assert [hit.section_id for hit in result.hits] == ["deep-work.a"]


def test_search_mixes_indexed_and_unindexed_documents(tmp_path: Path) -> None:
    """Cross-document search covers indexed docs (via the DB) and unindexed ones."""

    workspace = _workspace(
        tmp_path,
        _section("deep-work.a", "Storage", "storage text"),
    )
    write_sections(
        [
            Section(
                id="ddia.x",
                doc_id="ddia",
                title="Storage",
                level=1,
                heading_path=["Storage"],
                text="storage text",
            )
        ],
        workspace.sources_sections / "ddia",
    )
    # Only 'deep-work' is indexed; 'ddia' is served by the scan fallback.
    _build_index(workspace, "deep-work")

    everything = service.search_sections(workspace, "storage")

    assert sorted(hit.doc_id for hit in everything.hits) == ["ddia", "deep-work"]


def test_search_rejects_empty_query(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", "Alpha"))

    with pytest.raises(ReadingServiceError, match="at least one term"):
        service.search_sections(workspace, "   ")


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "..", "UP", "with space"])
def test_client_ids_that_are_not_slugs_are_rejected(tmp_path: Path, bad_id: str) -> None:
    """MCP tool ids are client-controlled and must never reach a filesystem path raw."""

    workspace = _workspace(tmp_path, _section("deep-work.a", "Alpha"))
    _plan(workspace, "deep-work.a")

    with pytest.raises(InvalidIdError):
        service.get_next_section(workspace, bad_id)
    with pytest.raises(InvalidIdError):
        service.get_section(workspace, bad_id, "deep-work.a")
    with pytest.raises(InvalidIdError):
        service.mark_read(workspace, bad_id)
    with pytest.raises(InvalidIdError):
        service.search_sections(workspace, "alpha", doc_id=bad_id)


def test_mark_read_with_traversal_plan_id_writes_nothing_outside(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", "Alpha"))
    _plan(workspace, "deep-work.a")
    # Where "../daily" would resolve to: reading_plans/../daily.json -> <root>/daily.json.
    escape_target = workspace.reading_plans_root.parent / "daily.json"
    escape_target.write_text('{"tampered": false}')

    with pytest.raises(InvalidIdError):
        service.mark_read(workspace, "../daily")

    # The traversal target is untouched — validation happened before any write.
    assert escape_target.read_text() == '{"tampered": false}'
