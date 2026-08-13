from __future__ import annotations

from pathlib import Path

from bookgraph.graph import build_section_graph, graph_path, read_graph, write_graph
from bookgraph.models import Section
from bookgraph.workspace import WorkspacePaths


def _section(
    section_id: str,
    title: str,
    level: int,
    *,
    prev_id: str | None = None,
    next_id: str | None = None,
) -> Section:
    return Section(
        id=section_id,
        doc_id="ddia",
        title=title,
        level=level,
        heading_path=[title],
        text="Body.",
        prev_id=prev_id,
        next_id=next_id,
    )


def _chapter_document() -> list[Section]:
    # Part I > (Chapter 1, Chapter 2 > Section 2.1), Part II
    return [
        _section("ddia.part-1", "Part I", 1, next_id="ddia.ch-1"),
        _section("ddia.ch-1", "Chapter 1", 2, prev_id="ddia.part-1", next_id="ddia.ch-2"),
        _section("ddia.ch-2", "Chapter 2", 2, prev_id="ddia.ch-1", next_id="ddia.sec-2-1"),
        _section("ddia.sec-2-1", "Section 2.1", 3, prev_id="ddia.ch-2", next_id="ddia.part-2"),
        _section("ddia.part-2", "Part II", 1, prev_id="ddia.sec-2-1"),
    ]


def test_hierarchy_links_parents_to_children_by_heading_level() -> None:
    graph = build_section_graph("ddia", _chapter_document())
    by_id = {node.id: node for node in graph.nodes}

    assert by_id["ddia.part-1"].parent_id is None
    assert by_id["ddia.part-1"].child_ids == ["ddia.ch-1", "ddia.ch-2"]
    assert by_id["ddia.ch-1"].parent_id == "ddia.part-1"
    assert by_id["ddia.ch-1"].child_ids == []
    assert by_id["ddia.ch-2"].parent_id == "ddia.part-1"
    assert by_id["ddia.ch-2"].child_ids == ["ddia.sec-2-1"]
    assert by_id["ddia.sec-2-1"].parent_id == "ddia.ch-2"
    # Part II closes Part I's subtree and becomes a fresh root.
    assert by_id["ddia.part-2"].parent_id is None
    assert by_id["ddia.part-2"].child_ids == []


def test_sequence_links_carry_through_from_the_manifest() -> None:
    graph = build_section_graph("ddia", _chapter_document())
    by_id = {node.id: node for node in graph.nodes}

    assert by_id["ddia.part-1"].prev_id is None
    assert by_id["ddia.part-1"].next_id == "ddia.ch-1"
    assert by_id["ddia.part-2"].next_id is None


def test_a_level_jump_still_nests_under_the_nearest_shallower_section() -> None:
    # level 1 then straight to level 3 (skipping 2): the deep section still nests.
    graph = build_section_graph(
        "ddia",
        [_section("ddia.a", "A", 1), _section("ddia.b", "B", 3)],
    )
    by_id = {node.id: node for node in graph.nodes}
    assert by_id["ddia.b"].parent_id == "ddia.a"
    assert by_id["ddia.a"].child_ids == ["ddia.b"]


def test_dangling_sequence_links_are_dropped() -> None:
    graph = build_section_graph(
        "ddia",
        [_section("ddia.only", "Only", 1, prev_id="ddia.ghost", next_id="ddia.ghost")],
    )
    node = graph.nodes[0]
    assert node.prev_id is None
    assert node.next_id is None


def test_write_and_read_graph_round_trips(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    graph = build_section_graph("ddia", _chapter_document())

    path = write_graph(graph, graph_path(workspace, "ddia"))

    assert path == workspace.indexes_root / "graph" / "ddia.json"
    assert read_graph(path) == graph
