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

    chapter = service.get_chapter_outline(workspace, "daily")

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
    assert (chapter.completed, chapter.total, chapter.done) == (2, 6, False)


def test_get_chapter_outline_honours_max_depth(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_plan(workspace, completed=[])

    chapter = service.get_chapter_outline(workspace, "daily", max_depth=2)

    assert [node.id for node in chapter.nodes] == ["doc.ch-1", "doc.s-1-1", "doc.s-1-2"]
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


def test_get_chapter_outline_raises_for_missing_plan(tmp_path: Path) -> None:
    with pytest.raises(PlanNotFoundError):
        service.get_chapter_outline(_workspace(tmp_path), "nope")
