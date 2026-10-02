"""Search and graph navigation tools: ``search``, outlines, related sections and
section context (re-exported by :mod:`bookgraph.mcp.service`)."""

from __future__ import annotations

from collections import Counter

from bookgraph.graph import SectionGraph, SectionNode, build_section_graph, chapter_span
from bookgraph.index import default_index_backend, tokenize
from bookgraph.mcp.concept_tools import (
    _section_summary,
)
from bookgraph.mcp.errors import (
    ReadingServiceError,
    SectionNotFoundError,
    SectionsNotFoundError,
)
from bookgraph.mcp.loading import (
    _load_doc_sections,
    _load_plan,
    _section_ref,
    _segmented_doc_ids,
    _validate_id,
)
from bookgraph.mcp.reading_tools import (
    get_section,
)
from bookgraph.mcp.views import (
    ChapterOutline,
    ConceptRef,
    Outline,
    OutlineNode,
    ProgressNode,
    RelatedSections,
    SearchHit,
    SearchResult,
    SectionContext,
    SectionRef,
    SectionTree,
)
from bookgraph.models import (
    Section,
)
from bookgraph.reading_plans import (
    next_sections,
)
from bookgraph.workspace import WorkspacePaths


def _snippet(text: str, terms: list[str], width: int = 160) -> str:
    """A short excerpt around the first matching term, else the text head."""

    lowered = text.lower()
    positions = [pos for pos in (lowered.find(term) for term in terms) if pos >= 0]
    if not positions:
        excerpt = text[:width]
        return excerpt + ("…" if len(text) > width else "")
    start = max(0, min(positions) - width // 3)
    excerpt = text[start : start + width]
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + width < len(text) else ""
    return prefix + excerpt + suffix


def _hits_from_sections(sections: list[Section], terms: list[str]) -> list[SearchHit]:
    """Score sections by a live scan — the fallback for unindexed documents."""

    hits: list[SearchHit] = []
    for section in sections:
        counts = Counter(tokenize(f"{section.title}\n{section.text}"))
        score = sum(counts[term] for term in terms)
        if score > 0:
            hits.append(
                SearchHit(
                    section_id=section.id,
                    doc_id=section.doc_id,
                    title=section.title,
                    score=float(score),
                    snippet=_snippet(section.text, terms),
                )
            )
    return hits


def search_sections(
    workspace: WorkspacePaths,
    query: str,
    doc_id: str | None = None,
    limit: int = 10,
) -> SearchResult:
    """Rank sections by relevance of the query terms in title and text.

    Uses the persisted index (built by ``bookgraph index build``) for indexed
    documents via the :class:`~bookgraph.index.IndexBackend`, and falls back to a
    live scan of ``sources/sections/<doc_id>/sections.jsonl`` for documents not
    yet indexed. Pass ``doc_id`` to search a single document, or omit it to search
    across every indexed/segmented document in the workspace (each hit carries its
    ``doc_id``).
    """

    terms = tokenize(query)
    if not terms:
        raise ReadingServiceError("search query must contain at least one term")
    if limit < 1:
        raise ReadingServiceError("limit must be at least 1")

    scoped = doc_id is not None
    if doc_id is not None:
        targets = [_validate_id(doc_id, "doc_id")]
    else:
        targets = _segmented_doc_ids(workspace)

    backend = default_index_backend()
    indexed = backend.indexed_doc_ids(workspace)
    db_targets = [current for current in targets if current in indexed]
    scan_targets = [current for current in targets if current not in indexed]

    hits: list[SearchHit] = []
    if db_targets:
        for hit in backend.search(workspace, terms, db_targets, limit):
            hits.append(
                SearchHit(
                    section_id=hit.section_id,
                    doc_id=hit.doc_id,
                    title=hit.title,
                    score=hit.score,
                    snippet=_snippet(hit.text, terms),
                )
            )

    for current_doc in scan_targets:
        try:
            hits.extend(_hits_from_sections(_load_doc_sections(workspace, current_doc), terms))
        except SectionsNotFoundError:
            if scoped:
                raise

    hits.sort(key=lambda hit: (-hit.score, hit.doc_id, hit.section_id))
    return SearchResult(query=query, hits=hits[:limit])


def _load_graph(workspace: WorkspacePaths, doc_id: str) -> SectionGraph:
    """Load a document's section graph, preferring the persisted index.

    Uses the :class:`~bookgraph.index.IndexBackend` when the document is indexed,
    and otherwise rebuilds the graph from ``sections.jsonl`` — so the graph/context
    tools work before ``bookgraph index build`` has run, and a missing/corrupt
    index degrades to the authoritative sections manifest.
    """

    _validate_id(doc_id, "doc_id")
    graph = default_index_backend().load_graph(workspace, doc_id)
    if graph is not None:
        return graph
    return build_section_graph(doc_id, _load_doc_sections(workspace, doc_id))


def _scoped_nodes(
    graph: SectionGraph, root_id: str | None, max_depth: int | None
) -> tuple[list[SectionNode], bool]:
    """The graph nodes under ``root_id`` (or the whole document) up to ``max_depth``.

    Depth is tree depth, not heading ``level`` (levels may skip): the scope's top —
    ``root_id`` itself, or every top-level section — is depth 1. Parents precede their
    children in reading order, so one pass assigns every in-scope node its depth.
    Returns the kept nodes in reading order and whether ``max_depth`` cut any off.
    """

    if max_depth is not None and max_depth < 1:
        raise ReadingServiceError("max_depth must be at least 1")
    if root_id is not None and all(node.id != root_id for node in graph.nodes):
        raise SectionNotFoundError(f"Section '{root_id}' not found in document '{graph.doc_id}'.")

    depths: dict[str, int] = {}
    kept: list[SectionNode] = []
    truncated = False
    for node in graph.nodes:
        if node.id == root_id or (root_id is None and node.parent_id is None):
            depth = 1
        elif node.parent_id is not None and node.parent_id in depths:
            depth = depths[node.parent_id] + 1
        else:
            continue
        depths[node.id] = depth
        if max_depth is not None and depth > max_depth:
            truncated = True
        else:
            kept.append(node)
    return kept, truncated


def _outline_node(node: SectionNode) -> OutlineNode:
    return OutlineNode(
        id=node.id,
        title=node.title,
        level=node.level,
        parent_id=node.parent_id,
        child_ids=list(node.child_ids),
    )


def get_outline(
    workspace: WorkspacePaths,
    doc_id: str,
    root_id: str | None = None,
    max_depth: int | None = None,
) -> Outline:
    """Return a document's section outline (hierarchy) in reading order.

    With no options this is the full document. ``root_id`` scopes the outline to that
    section's subtree (the section included); ``max_depth`` keeps only that many tree
    levels from the scope's top (``1`` = the top-level sections, or ``root_id`` alone).
    Large books produce huge full outlines, so agents should prefer a scoped call.
    """

    graph = _load_graph(workspace, doc_id)
    nodes, truncated = _scoped_nodes(graph, root_id, max_depth)
    return Outline(
        doc_id=doc_id,
        nodes=[_outline_node(node) for node in nodes],
        root_id=root_id,
        total_nodes=len(graph.nodes),
        truncated=truncated,
    )


def _ancestors(by_id: dict[str, SectionNode], node: SectionNode) -> list[SectionNode]:
    """``node``'s ancestors, root-first (top-level section down to the parent)."""

    ancestors: list[SectionNode] = []
    seen = {node.id}
    parent = by_id.get(node.parent_id) if node.parent_id is not None else None
    # ``seen`` guards against a cycle in a corrupt persisted graph.
    while parent is not None and parent.id not in seen:
        seen.add(parent.id)
        ancestors.append(parent)
        parent = by_id.get(parent.parent_id) if parent.parent_id is not None else None
    ancestors.reverse()
    return ancestors


def get_section_tree(
    workspace: WorkspacePaths,
    doc_id: str,
    section_id: str,
    include_siblings: bool = True,
    include_children: bool = True,
    sibling_window: int | None = 10,
) -> SectionTree:
    """Return a small outline around one section: breadcrumb, siblings, children.

    A cheap alternative to the full outline for "where am I in this book?" questions.
    For deeper nesting below the section, use ``get_outline(root_id=section_id)``.
    ``sibling_window`` keeps at most that many siblings on each side of the section
    (``None`` = all of them), so a flat document cannot turn this into the whole book.
    """

    if sibling_window is not None and sibling_window < 0:
        raise ReadingServiceError("sibling_window must be at least 0")

    graph = _load_graph(workspace, doc_id)
    by_id = {node.id: node for node in graph.nodes}
    node = by_id.get(section_id)
    if node is None:
        raise SectionNotFoundError(f"Section '{section_id}' not found in document '{doc_id}'.")

    siblings: list[SectionRef] = []
    siblings_truncated = False
    if include_siblings:
        parent_node = by_id.get(node.parent_id) if node.parent_id is not None else None
        if parent_node is not None:
            sibling_ids = parent_node.child_ids
        else:
            sibling_ids = [n.id for n in graph.nodes if n.parent_id is None]
        sibling_ids = [sid for sid in sibling_ids if sid in by_id]
        if sibling_window is not None and node.id in sibling_ids:
            here = sibling_ids.index(node.id)
            start = max(0, here - sibling_window)
            end = here + sibling_window + 1
            siblings_truncated = start > 0 or end < len(sibling_ids)
            sibling_ids = sibling_ids[start:end]
        siblings = [_section_ref(by_id[sid]) for sid in sibling_ids]

    children: list[SectionRef] = []
    if include_children:
        children = [_section_ref(by_id[cid]) for cid in node.child_ids if cid in by_id]

    return SectionTree(
        doc_id=doc_id,
        section=_section_ref(node),
        ancestors=[_section_ref(ancestor) for ancestor in _ancestors(by_id, node)],
        siblings=siblings,
        siblings_truncated=siblings_truncated,
        children=children,
    )


def get_chapter_outline(
    workspace: WorkspacePaths,
    plan_id: str,
    max_depth: int | None = 2,
    chapter_level: int | None = None,
) -> ChapterOutline:
    """Return the outline of the chapter a reading plan is currently in.

    The chapter is the scope ancestor of the plan's next unread section, resolved by the
    shared :func:`~bookgraph.graph.chapter_span` (so it always matches
    ``get_plan_progress``): the outermost ancestor by default, skipping a lone book-title
    root; pass ``chapter_level`` when chapters sit under parts. Its subtree comes back
    with a per-node ``read`` flag plus chapter and plan progress counts. ``max_depth``
    limits the subtree as in ``get_outline``; it defaults to ``2`` (the chapter and its
    direct subsections) so a chapter that turns out to be the whole book stays small —
    pass ``None`` for the full subtree.
    """

    if chapter_level is not None and chapter_level < 1:
        raise ReadingServiceError("chapter_level must be at least 1")
    _, plan = _load_plan(workspace, plan_id)
    pack = next_sections(plan)
    completed = set(plan.completed)
    result = ChapterOutline(
        plan_id=plan.plan_id,
        doc_id=plan.doc_id,
        plan_completed=sum(1 for section_id in plan.section_ids if section_id in completed),
        plan_total=len(plan.section_ids),
        done=pack.done,
    )
    if pack.done:
        return result

    current_id = pack.sections[0]
    graph = _load_graph(workspace, plan.doc_id)
    if all(node.id != current_id for node in graph.nodes):
        raise SectionNotFoundError(
            f"Reading plan '{plan_id}' references unknown section '{current_id}' "
            f"in document '{plan.doc_id}'."
        )
    span = chapter_span(graph.nodes, current_id, chapter_level=chapter_level)
    chapter = span.chapter
    # The span's members, not the raw subtree, bound the chapter — while a wrapper
    # heading (a lone book-title root, or with chapter_level any shallower heading such
    # as a part) is itself being read, the span is that heading alone.
    members = set(span.member_ids)
    full = [node for node in _scoped_nodes(graph, chapter.id, None)[0] if node.id in members]
    nodes = [node for node in _scoped_nodes(graph, chapter.id, max_depth)[0] if node.id in members]
    truncated = len(nodes) < len(full)
    in_chapter = [section_id for section_id in plan.section_ids if section_id in members]
    result.completed = sum(1 for section_id in in_chapter if section_id in completed)
    result.total = len(in_chapter)
    result.remaining = result.total - result.completed
    result.current_section_id = current_id
    result.chapter = _section_ref(chapter)
    result.nodes = [
        ProgressNode(**_outline_node(node).model_dump(), read=node.id in completed)
        for node in nodes
    ]
    result.truncated = truncated
    return result


def get_related(workspace: WorkspacePaths, doc_id: str, section_id: str) -> RelatedSections:
    """Return a section's structural neighbours: parent, prev, next, and children."""

    graph = _load_graph(workspace, doc_id)
    by_id = {node.id: node for node in graph.nodes}
    node = by_id.get(section_id)
    if node is None:
        raise SectionNotFoundError(f"Section '{section_id}' not found in document '{doc_id}'.")

    def ref(neighbour_id: str | None) -> SectionRef | None:
        neighbour = by_id.get(neighbour_id) if neighbour_id is not None else None
        if neighbour is None:
            return None
        return SectionRef(id=neighbour.id, title=neighbour.title, level=neighbour.level)

    return RelatedSections(
        doc_id=doc_id,
        section_id=section_id,
        parent=ref(node.parent_id),
        prev=ref(node.prev_id),
        next=ref(node.next_id),
        children=[child for child_id in node.child_ids if (child := ref(child_id)) is not None],
    )


def get_context(
    workspace: WorkspacePaths,
    doc_id: str,
    section_id: str,
    include_assets: bool = True,
) -> SectionContext:
    """Return a section's full content, graph neighbourhood, and its concepts.

    The concepts let a reader pivot from the current section to where each concept
    is discussed elsewhere (via ``get_concept``). They are empty for a document that
    has not been indexed (concepts have no live-scan fallback). ``include_assets``
    controls whether the embedded section carries its figures/tables (see ``get_section``).
    """

    # Resolve the section first so a missing id raises before any graph work.
    section = get_section(workspace, doc_id, section_id, include_assets=include_assets)
    related = get_related(workspace, doc_id, section_id)
    concepts = [
        ConceptRef(
            slug=node.slug,
            title=node.title,
            doc_count=node.doc_count,
            mention_count=node.mention_count,
            gloss=node.gloss,
            source=node.source,
        )
        for node in default_index_backend().section_concepts(workspace, doc_id, section_id)
    ]
    return SectionContext(
        section=section,
        related=related,
        concepts=concepts,
        summary=_section_summary(workspace, doc_id, section_id),
    )
