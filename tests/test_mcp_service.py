from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bookgraph.documents import write_document
from bookgraph.index.sqlite import SqliteIndexBackend, db_path
from bookgraph.mcp import service
from bookgraph.mcp.service import (
    InvalidIdError,
    PlanNotFoundError,
    ReadingServiceError,
    SectionNotFoundError,
    SectionsNotFoundError,
)
from bookgraph.models import CanonicalBlock, Document, ReadingPlan, Section
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


def _stage_asset(workspace: WorkspacePaths, doc_id: str, rel: str) -> Path:
    """Create a real asset file under sources/parsed/<doc_id>/ (as MinerU staging would)."""

    path = workspace.sources_parsed / doc_id / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff")  # minimal JPEG-ish bytes; content is irrelevant here
    return path


def _write_document_with_assets(workspace: WorkspacePaths, doc_id: str = "deep-work") -> None:
    """Write a parsed ``document.json`` and stage the image/table files it references."""

    _stage_asset(workspace, doc_id, "images/fig1.jpg")
    _stage_asset(workspace, doc_id, "nested/tbl1.jpg")
    document = Document(
        doc_id=doc_id,
        title="Deep Work",
        blocks=[
            CanonicalBlock(id="t0", type="title", text="Figures", order=0, page_idx=1),
            CanonicalBlock(
                id="img1",
                type="image",
                text="Figure 1. The pipeline.",
                asset_path="fig1.jpg",
                order=1,
                page_idx=1,
            ),
            CanonicalBlock(
                id="tbl1",
                type="table",
                text="Table 1. Results.",
                asset_path="nested/tbl1.jpg",
                order=2,
                page_idx=2,
            ),
        ],
    )
    write_document(document, workspace.sources_parsed / doc_id)


def test_get_section_returns_structured_assets(tmp_path: Path) -> None:
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Figure 1. The pipeline. Table 1. Results.",
        block_ids=["t0", "img1", "tbl1"],
    )
    workspace = _workspace(tmp_path, section)
    _write_document_with_assets(workspace)

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert [asset.type for asset in view.assets] == ["image", "table"]
    image, table = view.assets
    assert image.block_id == "img1"
    assert image.caption == "Figure 1. The pipeline."
    assert image.order == 1
    assert image.page_idx == 1
    # A bare filename resolves under the staged ``images/`` dir; a relative path is kept.
    assert image.path == str(workspace.sources_parsed / "deep-work" / "images" / "fig1.jpg")
    assert table.path == str(workspace.sources_parsed / "deep-work" / "nested" / "tbl1.jpg")
    # Prose is effectively just the captions, so the reader is warned to open the assets.
    assert view.notes


def test_get_section_include_assets_false_omits_assets(tmp_path: Path) -> None:
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Figure 1. The pipeline.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    _write_document_with_assets(workspace)

    view = service.get_section(workspace, "deep-work", "deep-work.figs", include_assets=False)

    assert view.assets == []
    assert view.notes == []


def test_get_section_without_parsed_document_has_no_assets(tmp_path: Path) -> None:
    # The existing fixtures only write sections.jsonl (no document.json) — assets stay empty.
    workspace = _workspace(tmp_path, _section("deep-work.a", "Alpha"))

    view = service.get_section(workspace, "deep-work", "deep-work.a")

    assert view.assets == []
    assert view.notes == []


def test_get_context_carries_section_assets(tmp_path: Path) -> None:
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Figure 1. The pipeline.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    _write_document_with_assets(workspace)

    context = service.get_context(workspace, "deep-work", "deep-work.figs")

    assert [asset.block_id for asset in context.section.assets] == ["img1"]


def _workspace_with_blocks(tmp_path: Path, *blocks: CanonicalBlock) -> WorkspacePaths:
    """A workspace whose single section owns exactly ``blocks`` (via ``document.json``)."""

    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Some genuine prose about the figures that is clearly longer than a caption.",
        block_ids=[block.id for block in blocks],
    )
    workspace = _workspace(tmp_path, section)
    write_document(
        Document(doc_id="deep-work", title="Deep Work", blocks=list(blocks)),
        workspace.sources_parsed / "deep-work",
    )
    return workspace


def test_assets_drop_unresolvable_references(tmp_path: Path) -> None:
    # URL, absolute path, workspace-escaping relative path, and a markdown table rendered
    # inline (no asset_path, no src) must NOT surface as AssetRefs — an AssetRef must always
    # point at a real workspace file.
    workspace = _workspace_with_blocks(
        tmp_path,
        CanonicalBlock(id="url", type="image", metadata={"src": "https://example.com/x.png"}),
        CanonicalBlock(id="abs", type="image", asset_path="/etc/passwd"),
        CanonicalBlock(id="escape", type="image", asset_path="../../secret.jpg"),
        CanonicalBlock(id="inline_tbl", type="table", text="| a | b |"),
        CanonicalBlock(id="ok", type="image", asset_path="real.jpg", order=9),
    )
    _stage_asset(workspace, "deep-work", "images/real.jpg")  # only the "ok" block has a file

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert [asset.block_id for asset in view.assets] == ["ok"]
    assert view.assets[0].path == str(
        workspace.sources_parsed / "deep-work" / "images" / "real.jpg"
    )


def test_asset_notes_not_raised_for_genuine_short_prose(tmp_path: Path) -> None:
    # A short but real sentence next to a one-word caption must not trip the caption-only
    # warning (regression for the length-only heuristic).
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="See the diagram for how the ingest pipeline is wired end to end.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[
                CanonicalBlock(id="img1", type="image", text="Pipeline", asset_path="p.jpg")
            ],
        ),
        workspace.sources_parsed / "deep-work",
    )
    _stage_asset(workspace, "deep-work", "images/p.jpg")

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets  # the image is still surfaced
    assert view.notes == []  # but the prose is genuine, so no caption-only warning


def test_asset_dropped_when_referenced_file_is_missing(tmp_path: Path) -> None:
    # asset_path names a file the parser never staged — no AssetRef should be emitted.
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[CanonicalBlock(id="img1", type="image", asset_path="ghost.jpg")],
        ),
        workspace.sources_parsed / "deep-work",
    )

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets == []


def test_asset_dropped_for_symlink_escaping_the_workspace(tmp_path: Path) -> None:
    # A symlink under images/ that points outside the workspace must not resolve to a
    # returnable path — the containment check follows symlinks, not just the lexical path.
    outside = tmp_path / "outside_secret.jpg"
    outside.write_bytes(b"\xff\xd8\xff")
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    images_dir = workspace.sources_parsed / "deep-work" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "evil.jpg").symlink_to(outside)
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[CanonicalBlock(id="img1", type="image", asset_path="evil.jpg")],
        ),
        workspace.sources_parsed / "deep-work",
    )

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets == []


def test_doc_blocks_cache_invalidates_on_same_mtime_size_change(tmp_path: Path) -> None:
    service._DOC_BLOCKS_CACHE.clear()
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    _stage_asset(workspace, "deep-work", "images/a.jpg")
    document_path = workspace.sources_parsed / "deep-work" / "document.json"

    def write(caption: str) -> None:
        write_document(
            Document(
                doc_id="deep-work",
                title="Deep Work",
                blocks=[
                    CanonicalBlock(id="img1", type="image", text=caption, asset_path="a.jpg")
                ],
            ),
            workspace.sources_parsed / "deep-work",
        )

    write("First caption.")
    first = service.get_section(workspace, "deep-work", "deep-work.figs")
    assert first.assets[0].caption == "First caption."
    mtime = document_path.stat().st_mtime

    # Rewrite with a different length, then force the *same* mtime: only the size differs.
    write("A second, noticeably longer caption than the first one.")
    os.utime(document_path, (mtime, mtime))

    second = service.get_section(workspace, "deep-work", "deep-work.figs")
    assert second.assets[0].caption == "A second, noticeably longer caption than the first one."


def test_doc_blocks_cache_is_bounded(tmp_path: Path) -> None:
    service._DOC_BLOCKS_CACHE.clear()
    workspace = WorkspacePaths(tmp_path)
    for i in range(service._DOC_BLOCKS_CACHE_MAX + 10):
        doc_id = f"doc{i}"
        write_document(
            Document(doc_id=doc_id, title=doc_id, blocks=[]),
            workspace.sources_parsed / doc_id,
        )
        service._load_doc_blocks(workspace, doc_id)

    assert len(service._DOC_BLOCKS_CACHE) <= service._DOC_BLOCKS_CACHE_MAX


def test_asset_with_embedded_nul_degrades_instead_of_crashing(tmp_path: Path) -> None:
    # A corrupt/adversarial document.json can carry a NUL byte in a path; resolving it
    # raises ValueError, which must degrade to "no asset", not crash the section fetch.
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[CanonicalBlock(id="img1", type="image", asset_path="fig\x00.jpg")],
        ),
        workspace.sources_parsed / "deep-work",
    )

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets == []


def test_get_next_section_include_assets_toggle(tmp_path: Path) -> None:
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    _stage_asset(workspace, "deep-work", "images/fig1.jpg")
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[CanonicalBlock(id="img1", type="image", asset_path="fig1.jpg")],
        ),
        workspace.sources_parsed / "deep-work",
    )
    _plan(workspace, "deep-work.figs")

    with_assets = service.get_next_section(workspace, "daily")
    assert [a.block_id for a in with_assets.sections[0].assets] == ["img1"]

    without = service.get_next_section(workspace, "daily", include_assets=False)
    assert without.sections[0].assets == []


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
