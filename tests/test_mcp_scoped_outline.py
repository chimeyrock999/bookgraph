"""Scoped outline queries (#39): subtree/depth-limited outlines, section trees,
and the reading plan's current-chapter outline."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.index.sqlite import SqliteIndexBackend
from bookgraph.mcp import service
from bookgraph.mcp.service import (
    PlanNotFoundError,
    ReadingServiceError,
    SectionNotFoundError,
    SectionsNotFoundError,
)
from bookgraph.models import ReadingPlan, Section
from bookgraph.reading_plans import write_reading_plan
from bookgraph.sections import write_sections
from bookgraph.workspace import WorkspacePaths

# Reading order with hierarchy:
#   Ch 1
#     1.1
#       1.1.1
#     1.2
#   Ch 2
#     2.1
_LAYOUT = [
    ("doc.ch-1", "Chapter 1", 1),
    ("doc.s-1-1", "Section 1.1", 2),
    ("doc.s-1-1-1", "Section 1.1.1", 3),
    ("doc.s-1-2", "Section 1.2", 2),
    ("doc.ch-2", "Chapter 2", 1),
    ("doc.s-2-1", "Section 2.1", 2),
]
_ALL_IDS = [section_id for section_id, _, _ in _LAYOUT]


def _sections() -> list[Section]:
    sections = []
    for index, (section_id, title, level) in enumerate(_LAYOUT):
        sections.append(
            Section(
                id=section_id,
                doc_id="doc",
                title=title,
                level=level,
                heading_path=[title],
                text="Body.",
                prev_id=_ALL_IDS[index - 1] if index > 0 else None,
                next_id=_ALL_IDS[index + 1] if index + 1 < len(_ALL_IDS) else None,
            )
        )
    return sections


def _workspace(tmp_path: Path, *, build_index: bool = False) -> WorkspacePaths:
    workspace = WorkspacePaths(tmp_path)
    sections = _sections()
    write_sections(sections, workspace.sources_sections / "doc")
    if build_index:
        SqliteIndexBackend().build_document(workspace, "doc", "Doc", sections)
    return workspace


def _write_plan(workspace: WorkspacePaths, completed: list[str]) -> None:
    write_reading_plan(
        ReadingPlan(plan_id="daily", doc_id="doc", section_ids=_ALL_IDS, completed=completed),
        workspace.reading_plans_root / "daily.json",
    )


# --- get_outline(root_id=..., max_depth=...) ---------------------------------


def test_get_outline_defaults_to_the_full_document(tmp_path: Path) -> None:
    outline = service.get_outline(_workspace(tmp_path), "doc")

    assert [node.id for node in outline.nodes] == _ALL_IDS
    assert outline.root_id is None
    assert outline.total_nodes == 6
    assert outline.truncated is False


@pytest.mark.parametrize("build_index", [False, True])
def test_get_outline_max_depth_limits_tree_depth(tmp_path: Path, build_index: bool) -> None:
    workspace = _workspace(tmp_path, build_index=build_index)

    chapters = service.get_outline(workspace, "doc", max_depth=1)
    assert [node.id for node in chapters.nodes] == ["doc.ch-1", "doc.ch-2"]
    assert chapters.truncated is True
    assert chapters.total_nodes == 6
    # child_ids stay complete so a client can drill into a node with root_id.
    assert chapters.nodes[0].child_ids == ["doc.s-1-1", "doc.s-1-2"]

    two_levels = service.get_outline(workspace, "doc", max_depth=2)
    assert "doc.s-1-1-1" not in [node.id for node in two_levels.nodes]
    assert len(two_levels.nodes) == 5


def test_get_outline_max_depth_counts_tree_depth_not_heading_level(tmp_path: Path) -> None:
    # Heading levels jump 1 -> 3; the level-3 section is still one step below its parent.
    workspace = WorkspacePaths(tmp_path)
    write_sections(
        [
            Section(
                id="doc.a", doc_id="doc", title="A", level=1, heading_path=["A"],
                text="x", next_id="doc.b",
            ),
            Section(
                id="doc.b", doc_id="doc", title="B", level=3, heading_path=["B"],
                text="x", prev_id="doc.a",
            ),
        ],
        workspace.sources_sections / "doc",
    )

    outline = service.get_outline(workspace, "doc", max_depth=2)

    assert [node.id for node in outline.nodes] == ["doc.a", "doc.b"]
    assert outline.truncated is False


def test_get_outline_root_id_scopes_to_a_subtree(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    subtree = service.get_outline(workspace, "doc", root_id="doc.ch-1")
    assert [node.id for node in subtree.nodes] == [
        "doc.ch-1",
        "doc.s-1-1",
        "doc.s-1-1-1",
        "doc.s-1-2",
    ]
    assert subtree.root_id == "doc.ch-1"
    assert subtree.truncated is False

    shallow = service.get_outline(workspace, "doc", root_id="doc.ch-1", max_depth=2)
    assert [node.id for node in shallow.nodes] == ["doc.ch-1", "doc.s-1-1", "doc.s-1-2"]
    assert shallow.truncated is True


def test_get_outline_rejects_unknown_root_and_bad_depth(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(SectionNotFoundError):
        service.get_outline(workspace, "doc", root_id="doc.ghost")
    with pytest.raises(ReadingServiceError, match="max_depth"):
        service.get_outline(workspace, "doc", max_depth=0)


# --- get_section_tree ----------------------------------------------------------


def test_get_section_tree_returns_breadcrumb_siblings_and_children(tmp_path: Path) -> None:
    tree = service.get_section_tree(_workspace(tmp_path), "doc", "doc.s-1-1")

    assert tree.section.id == "doc.s-1-1"
    assert [ref.id for ref in tree.ancestors] == ["doc.ch-1"]
    # Siblings include the section itself so its position is visible.
    assert [ref.id for ref in tree.siblings] == ["doc.s-1-1", "doc.s-1-2"]
    assert [ref.id for ref in tree.children] == ["doc.s-1-1-1"]


def test_get_section_tree_ancestors_run_root_first(tmp_path: Path) -> None:
    tree = service.get_section_tree(_workspace(tmp_path), "doc", "doc.s-1-1-1")

    assert [ref.id for ref in tree.ancestors] == ["doc.ch-1", "doc.s-1-1"]
    assert [ref.id for ref in tree.siblings] == ["doc.s-1-1-1"]
    assert tree.children == []


def test_get_section_tree_top_level_siblings_are_the_chapters(tmp_path: Path) -> None:
    tree = service.get_section_tree(_workspace(tmp_path), "doc", "doc.ch-2")

    assert tree.ancestors == []
    assert [ref.id for ref in tree.siblings] == ["doc.ch-1", "doc.ch-2"]


def test_get_section_tree_can_omit_siblings_and_children(tmp_path: Path) -> None:
    tree = service.get_section_tree(
        _workspace(tmp_path),
        "doc",
        "doc.s-1-1",
        include_siblings=False,
        include_children=False,
    )

    assert [ref.id for ref in tree.ancestors] == ["doc.ch-1"]
    assert tree.siblings == []
    assert tree.children == []


def test_get_section_tree_raises_for_unknown_section(tmp_path: Path) -> None:
    with pytest.raises(SectionNotFoundError):
        service.get_section_tree(_workspace(tmp_path), "doc", "doc.ghost")


# --- get_chapter_outline -------------------------------------------------------


def test_get_chapter_outline_scopes_to_the_current_chapter(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_plan(workspace, completed=["doc.ch-1", "doc.s-1-1"])

    chapter = service.get_chapter_outline(workspace, "daily", max_depth=None)

    assert chapter.plan_id == "daily"
    assert chapter.doc_id == "doc"
    assert chapter.current_section_id == "doc.s-1-1-1"
    assert chapter.chapter is not None and chapter.chapter.id == "doc.ch-1"
    assert [node.id for node in chapter.nodes] == [
        "doc.ch-1",
        "doc.s-1-1",
        "doc.s-1-1-1",
        "doc.s-1-2",
    ]
    assert {node.id: node.read for node in chapter.nodes} == {
        "doc.ch-1": True,
        "doc.s-1-1": True,
        "doc.s-1-1-1": False,
        "doc.s-1-2": False,
    }
    # Chapter counts are scoped to the chapter's subtree; plan counts are plan-wide.
    assert (chapter.completed, chapter.remaining, chapter.total) == (2, 2, 4)
    assert (chapter.plan_completed, chapter.plan_total, chapter.done) == (2, 6, False)


def test_get_chapter_outline_defaults_to_two_levels(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_plan(workspace, completed=[])

    chapter = service.get_chapter_outline(workspace, "daily")

    assert [node.id for node in chapter.nodes] == ["doc.ch-1", "doc.s-1-1", "doc.s-1-2"]
    assert chapter.truncated is True
    # Counts still cover the whole chapter subtree, not just the returned nodes.
    assert chapter.total == 4


def test_get_chapter_outline_honours_max_depth(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_plan(workspace, completed=[])

    chapter = service.get_chapter_outline(workspace, "daily", max_depth=1)

    assert [node.id for node in chapter.nodes] == ["doc.ch-1"]
    assert chapter.truncated is True


def test_get_chapter_outline_moves_to_the_next_chapter(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_plan(workspace, completed=_ALL_IDS[:4])

    chapter = service.get_chapter_outline(workspace, "daily")

    assert chapter.current_section_id == "doc.ch-2"
    assert [node.id for node in chapter.nodes] == ["doc.ch-2", "doc.s-2-1"]


def test_get_chapter_outline_when_plan_is_done(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_plan(workspace, completed=_ALL_IDS)

    chapter = service.get_chapter_outline(workspace, "daily")

    assert chapter.done is True
    assert chapter.current_section_id is None
    assert chapter.chapter is None
    assert chapter.nodes == []
    assert (chapter.completed, chapter.total) == (0, 0)
    assert (chapter.plan_completed, chapter.plan_total) == (6, 6)


def test_get_chapter_outline_raises_for_missing_plan(tmp_path: Path) -> None:
    with pytest.raises(PlanNotFoundError):
        service.get_chapter_outline(_workspace(tmp_path), "nope")


def _book_root_workspace(tmp_path: Path) -> WorkspacePaths:
    """One ``# Book`` root over two chapters, each with two sections (7 sections)."""

    layout = [("bk.book", "Book", 1)]
    for chapter in (1, 2):
        layout.append((f"bk.ch-{chapter}", f"Chapter {chapter}", 2))
        layout += [(f"bk.s-{chapter}-{n}", f"Section {chapter}.{n}", 3) for n in (1, 2)]
    workspace = WorkspacePaths(tmp_path)
    write_sections(
        [
            Section(id=sid, doc_id="bk", title=title, level=level, heading_path=[title],
                    text="x")
            for sid, title, level in layout
        ],
        workspace.sources_sections / "bk",
    )
    write_reading_plan(
        ReadingPlan(
            plan_id="bk", doc_id="bk", section_ids=[sid for sid, _, _ in layout],
            completed=["bk.book", "bk.ch-1", "bk.s-1-1", "bk.s-1-2"],
        ),
        workspace.reading_plans_root / "bk.json",
    )
    return workspace


def test_get_chapter_outline_skips_a_lone_book_root(tmp_path: Path) -> None:
    # One ``# Book`` root above every chapter: the scope is the chapter, not the book
    # (the shared resolve_chapter rule, so this matches get_plan_progress).
    chapter = service.get_chapter_outline(_book_root_workspace(tmp_path), "bk")

    assert chapter.current_section_id == "bk.ch-2"
    assert chapter.chapter is not None and chapter.chapter.id == "bk.ch-2"
    assert [node.id for node in chapter.nodes] == ["bk.ch-2", "bk.s-2-1", "bk.s-2-2"]
    assert (chapter.completed, chapter.remaining, chapter.total) == (0, 3, 3)


def test_get_chapter_outline_keeps_the_lone_root_while_it_is_being_read(
    tmp_path: Path,
) -> None:
    workspace = _book_root_workspace(tmp_path)
    write_reading_plan(
        ReadingPlan(
            plan_id="bk", doc_id="bk",
            section_ids=["bk.book", "bk.ch-1", "bk.s-1-1", "bk.s-1-2"],
        ),
        workspace.reading_plans_root / "bk.json",
    )

    chapter = service.get_chapter_outline(workspace, "bk")

    # The root is the scope, and the default depth keeps it to the root + chapters.
    assert chapter.chapter is not None and chapter.chapter.id == "bk.book"
    assert [node.id for node in chapter.nodes] == ["bk.book", "bk.ch-1", "bk.ch-2"]
    assert chapter.truncated is True


def test_get_chapter_outline_chapter_level_picks_nested_chapters(tmp_path: Path) -> None:
    chapter = service.get_chapter_outline(
        _book_root_workspace(tmp_path), "bk", chapter_level=2
    )

    assert chapter.current_section_id == "bk.ch-2"
    assert chapter.chapter is not None and chapter.chapter.id == "bk.ch-2"
    assert [node.id for node in chapter.nodes] == ["bk.ch-2", "bk.s-2-1", "bk.s-2-2"]
    assert (chapter.completed, chapter.remaining, chapter.total) == (0, 3, 3)


def test_get_chapter_outline_rejects_bad_chapter_level(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_plan(workspace, completed=[])

    with pytest.raises(ReadingServiceError, match="chapter_level"):
        service.get_chapter_outline(workspace, "daily", chapter_level=0)


def test_get_chapter_outline_raises_for_a_stale_plan(tmp_path: Path) -> None:
    # The plan predates a re-segment: its next unread section no longer exists.
    workspace = _workspace(tmp_path)
    write_reading_plan(
        ReadingPlan(plan_id="daily", doc_id="doc", section_ids=["doc.gone", *_ALL_IDS]),
        workspace.reading_plans_root / "daily.json",
    )

    with pytest.raises(SectionNotFoundError, match="doc.gone"):
        service.get_chapter_outline(workspace, "daily")


def test_get_section_tree_raises_for_unknown_document(tmp_path: Path) -> None:
    with pytest.raises(SectionsNotFoundError):
        service.get_section_tree(_workspace(tmp_path), "ghost", "ghost.a")


def _flat_workspace(tmp_path: Path, count: int) -> WorkspacePaths:
    """A page/token-fallback style document: every section is top-level."""

    workspace = WorkspacePaths(tmp_path)
    write_sections(
        [
            Section(id=f"flat.p-{n}", doc_id="flat", title=f"Page {n}", level=1,
                    heading_path=[f"Page {n}"], text="x")
            for n in range(count)
        ],
        workspace.sources_sections / "flat",
    )
    return workspace


def test_get_section_tree_windows_siblings_in_a_flat_document(tmp_path: Path) -> None:
    workspace = _flat_workspace(tmp_path, 50)

    tree = service.get_section_tree(workspace, "flat", "flat.p-20", sibling_window=2)
    assert [ref.id for ref in tree.siblings] == [f"flat.p-{n}" for n in range(18, 23)]
    assert tree.siblings_truncated is True

    edge = service.get_section_tree(workspace, "flat", "flat.p-0", sibling_window=2)
    assert [ref.id for ref in edge.siblings] == ["flat.p-0", "flat.p-1", "flat.p-2"]

    default = service.get_section_tree(workspace, "flat", "flat.p-20")
    assert len(default.siblings) == 21

    everything = service.get_section_tree(workspace, "flat", "flat.p-20", sibling_window=None)
    assert len(everything.siblings) == 50
    assert everything.siblings_truncated is False


def test_get_section_tree_rejects_negative_sibling_window(tmp_path: Path) -> None:
    with pytest.raises(ReadingServiceError, match="sibling_window"):
        service.get_section_tree(_workspace(tmp_path), "doc", "doc.ch-1", sibling_window=-1)
