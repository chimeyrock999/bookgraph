"""Persistent section graph backing the graph/context MCP tools.

The segment stage writes ``sources/sections/<doc_id>/sections.jsonl`` in reading
order; this module derives a per-document graph under
``indexes/graph/<doc_id>.json`` capturing how sections relate structurally:

- **hierarchy** — each section's parent is the nearest preceding section with a
  smaller heading ``level`` (so a chapter parents its subsections), with the
  matching ``child_ids`` on the parent.
- **sequence** — linear reading-order neighbours (``prev_id`` / ``next_id``),
  carried straight through from the sections manifest.

The graph is fully regenerable from the sections manifest and depends only on it
(never on the compiled wiki), so it can be built the moment a document is
segmented. It stores each section's ``title``/``level``/``heading_path`` so the
graph/context tools can render an outline without re-reading the manifest.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from bookgraph.models import Section
from bookgraph.workspace import WorkspacePaths


class SectionNode(BaseModel):
    """One section and its structural links within a document graph."""

    id: str
    title: str
    level: int
    heading_path: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    prev_id: str | None = None
    next_id: str | None = None
    child_ids: list[str] = Field(default_factory=list)


class SectionGraph(BaseModel):
    """Structural graph for one document's sections, in reading order."""

    doc_id: str
    nodes: list[SectionNode] = Field(default_factory=list)


def graph_path(workspace: WorkspacePaths, doc_id: str) -> Path:
    """Canonical location of a document's section graph."""

    return workspace.indexes_root / "graph" / f"{doc_id}.json"


def build_section_graph(doc_id: str, sections: list[Section]) -> SectionGraph:
    """Build the hierarchy + sequence graph from a document's sections.

    Parent resolution is a single reading-order pass with a stack of open
    ancestors: before placing a section, ancestors whose ``level`` is greater than
    or equal to the section's are popped, so the parent is always the nearest
    strictly-shallower preceding section. This mirrors how nested headings nest
    regardless of whether levels increase by exactly one.
    """

    nodes = [
        SectionNode(
            id=section.id,
            title=section.title,
            level=section.level,
            heading_path=list(section.heading_path),
            prev_id=section.prev_id,
            next_id=section.next_id,
        )
        for section in sections
    ]
    by_id = {node.id: node for node in nodes}

    stack: list[SectionNode] = []
    for node in nodes:
        while stack and stack[-1].level >= node.level:
            stack.pop()
        if stack:
            parent = stack[-1]
            node.parent_id = parent.id
            parent.child_ids.append(node.id)
        stack.append(node)

    # Guard against a manifest whose prev/next ids reference sections outside this
    # document (a corrupt or hand-edited manifest); such links are dropped rather
    # than silently claiming an edge to a node the graph does not contain.
    for node in nodes:
        if node.prev_id is not None and node.prev_id not in by_id:
            node.prev_id = None
        if node.next_id is not None and node.next_id not in by_id:
            node.next_id = None

    return SectionGraph(doc_id=doc_id, nodes=nodes)


def write_graph(graph: SectionGraph, path: Path) -> Path:
    """Persist a section graph to ``path``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(graph.model_dump_json(indent=2) + "\n")
    return path


def read_graph(path: Path) -> SectionGraph:
    """Load a section graph written by :func:`write_graph`."""

    return SectionGraph.model_validate_json(path.read_text())
