"""Section graph model + builder backing the graph/context MCP tools.

The segment stage writes ``sources/sections/<doc_id>/sections.jsonl`` in reading
order; :func:`build_section_graph` derives from it how sections relate
structurally:

- **hierarchy** — each section's parent is the nearest preceding section with a
  smaller heading ``level`` (so a chapter parents its subsections), with the
  matching ``child_ids`` on the parent.
- **sequence** — linear reading-order neighbours (``prev_id`` / ``next_id``),
  carried straight through from the sections manifest.

The graph is fully regenerable from the sections manifest and depends only on it
(never on the compiled wiki). It is persisted by the index stage (see
:mod:`bookgraph.index`) and rebuilt on demand from the manifest when a document
has not been indexed yet.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from bookgraph.models import Section


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


def resolve_chapter(
    nodes: list[SectionNode], section_id: str, *, chapter_level: int | None = None
) -> SectionNode:
    """Return the "chapter" (scope ancestor-or-self) that contains ``section_id``.

    With ``chapter_level`` it is the nearest ancestor-or-self whose heading ``level`` is
    at most ``chapter_level`` (falling back to the outermost ancestor). Without it, it is
    the outermost ancestor — except that a **lone** top-level root (one ``# Book Title``
    heading above every chapter, common for Markdown/EPUB ingestion) is skipped one level
    down the path, so the scope is the chapter rather than the whole book. The root
    itself stays the scope while it is the section being read.

    Shared by every API that needs "the chapter the reader is in", so they never
    disagree. ``nodes`` come from :func:`build_section_graph` or the persisted index.
    """

    by_id = {node.id: node for node in nodes}
    if section_id not in by_id:
        raise ValueError(f"section '{section_id}' is not in the graph")

    path = [by_id[section_id]]  # ancestor-or-self chain, innermost first
    while path[-1].parent_id is not None and path[-1].parent_id in by_id:
        path.append(by_id[path[-1].parent_id])

    if chapter_level is not None:
        return next((node for node in path if node.level <= chapter_level), path[-1])

    root = path[-1]
    lone_root = sum(1 for node in nodes if node.parent_id is None) == 1
    return path[-2] if lone_root and len(path) > 1 else root
