"""Concept graph and annotation tools: ``get_concept``, ``concept_hygiene``,
``annotate_section`` (re-exported by :mod:`bookgraph.mcp.service`)."""

from __future__ import annotations

from datetime import UTC, datetime

from bookgraph.annotations import (
    annotation_path,
    build_annotation,
    read_annotation,
    write_annotation,
)
from bookgraph.concept_hygiene import (
    DEFAULT_MERGE_THRESHOLD,
    lint_concepts,
    review_queue,
    suggest_merges,
)
from bookgraph.concept_registry import ConceptRegistry, read_registry
from bookgraph.index import ConceptMention, default_index_backend
from bookgraph.mcp.errors import (
    ConceptNotFoundError,
    ReadingServiceError,
)
from bookgraph.mcp.loading import (
    _validate_id,
)
from bookgraph.mcp.reading_tools import (
    get_section,
)
from bookgraph.mcp.views import (
    AnnotationResult,
    ConceptHygieneReport,
    ConceptInput,
    ConceptMentionView,
    ConceptView,
)
from bookgraph.models import (
    AnnotatedConcept,
)
from bookgraph.workspace import WorkspacePaths


def _section_summary(workspace: WorkspacePaths, doc_id: str, section_id: str) -> str:
    """The Tier-2 summary for a section, read straight from the annotation file.

    Reading the single file (not the index) is what makes an ``annotate_section``
    summary visible immediately, before the next ``index build``. A missing or corrupt
    annotation is simply "no summary".
    """

    path = annotation_path(workspace.annotations_root, doc_id, section_id)
    if not path.is_file():
        return ""
    try:
        return read_annotation(path).summary
    except (OSError, ValueError):
        return ""


def get_concept(
    workspace: WorkspacePaths, concept: str, include_annotations: bool = False
) -> ConceptView:
    """Return a concept and its cross-book backlink mentions from the index.

    ``include_annotations`` selects the read mode. Left ``False`` (the default), each
    mention is a compact backlink pointer — doc/section/title plus its section-scoped
    gloss and source — cheap enough for graph traversal. Set ``True`` for the
    concept-detail view: each mention additionally carries its section's Tier-2
    annotation ``summary``, turning a concept with several mentions into a readable,
    source-grounded note instead of a set of short glosses. Every summary stays tied to
    the section it came from, so the long-form context remains provenance-aware.
    """

    requested = _validate_id(concept, "concept")
    registry = _load_registry(workspace)
    slug = registry.resolve(requested)
    record = registry.record(slug)
    backend = default_index_backend()
    result = backend.get_concept(workspace, slug)

    # Fold in mentions still indexed under an alias slug. After a full rebuild there
    # are none (build rewrites them to the canonical); in the window between a registry
    # edit and the rebuild this keeps the canonical view complete — including when the
    # canonical itself has no rows yet.
    raw_mentions: list[ConceptMention] = list(result.mentions) if result is not None else []
    seen = {(m.doc_id, m.section_id) for m in raw_mentions}
    fallback_title: str | None = None
    for alias in record.aliases if record is not None else []:
        stale = backend.get_concept(workspace, alias)
        if stale is None:
            continue
        fallback_title = fallback_title or stale.node.title
        for mention in stale.mentions:
            if (mention.doc_id, mention.section_id) not in seen:
                seen.add((mention.doc_id, mention.section_id))
                raw_mentions.append(mention.model_copy(update={"raw_slug": alias}))
    if result is None and not raw_mentions:
        raise ConceptNotFoundError(
            f"Concept '{slug}' not found. Run 'bookgraph index build' then "
            "'bookgraph index concepts'."
        )
    # Stable sort: group by document, keeping each source's reading order.
    raw_mentions.sort(key=lambda m: m.doc_id)

    if record is not None:
        title = record.title
    elif result is not None:
        title = result.node.title
    else:  # unreachable: aliases imply a record
        title = fallback_title or slug
    mentions = [
        ConceptMentionView(
            doc_id=mention.doc_id,
            section_id=mention.section_id,
            title=mention.title,
            gloss=mention.gloss,
            source=mention.source,
            # The compact card omits the summary to stay lightweight; the detail view
            # surfaces it so the mention reads as long-form, source-grounded context.
            summary=mention.summary if include_annotations else "",
            raw_slug=mention.raw_slug,
        )
        for mention in raw_mentions
    ]
    aliases = list(record.aliases) if record is not None else []
    for mention in raw_mentions:
        if mention.raw_slug and mention.raw_slug not in aliases:
            aliases.append(mention.raw_slug)
    return ConceptView(
        slug=slug,
        title=title,
        doc_count=len({m.doc_id for m in raw_mentions}),
        mention_count=len(raw_mentions),
        # Count from the raw backend mentions, not the (possibly redacted) view list:
        # the backend returns summaries regardless of the flag, so the compact card can
        # still signal "this concept carries N annotated sections" — a cheap cue for an
        # agent deciding whether an include_annotations=True call is worth it.
        annotated_mention_count=sum(1 for m in raw_mentions if m.summary),
        aliases=aliases,
        canonical=record is not None,
        resolved_from=requested if requested != slug else None,
        mentions=mentions,
    )


def _load_registry(workspace: WorkspacePaths) -> ConceptRegistry:
    try:
        return read_registry(workspace.concept_registry)
    except ValueError as exc:
        raise ReadingServiceError(str(exc)) from exc


def concept_hygiene(
    workspace: WorkspacePaths,
    limit: int = 20,
    threshold: float = DEFAULT_MERGE_THRESHOLD,
) -> ConceptHygieneReport:
    """Merge suggestions, lint findings, and the agent-concept review queue.

    Read-only: it reports over the built index and the concept registry. ``limit``
    caps each list (``merge_suggestions``, ``lint``, ``review_queue``) independently;
    ``threshold`` is the minimum merge-suggestion score (0–1).
    """

    if limit < 1:
        raise ReadingServiceError("limit must be at least 1")
    if not 0 <= threshold <= 1:
        raise ReadingServiceError("threshold must be between 0 and 1")
    registry = _load_registry(workspace)
    concepts = default_index_backend().concepts(workspace)
    nodes = [concept.node for concept in concepts]
    return ConceptHygieneReport(
        concept_count=len(concepts),
        merge_suggestions=suggest_merges(nodes, registry, threshold=threshold, limit=limit),
        lint=lint_concepts(concepts, registry)[:limit],
        review_queue=review_queue(concepts, registry)[:limit],
    )


def annotate_section(
    workspace: WorkspacePaths,
    doc_id: str,
    section_id: str,
    concepts: list[ConceptInput] | None = None,
    summary: str = "",
    model: str | None = None,
) -> AnnotationResult:
    """Write a Tier-2 annotation for one section (the reinforcement loop).

    Records the agent's authoritative concept edge set + a prose summary as a source
    artifact (``annotations/<doc_id>/<section_id>.json``). It is **deferred**: it does
    not touch the index — the summary shows immediately via ``get_context``, while the
    concept edges (and their prune of Tier-1 false positives) take effect on the next
    ``bookgraph index build <doc_id>``. See ``docs/cli/annotations.md``.

    ``concepts`` has three intents that the next build treats distinctly: **omit it**
    (``None``) to leave the section's Tier-1 concepts untouched (e.g. a summary-only
    annotation); pass ``[]`` to **prune** the section's concepts (the tokenizer-false-
    positive fix); pass a list to make it the **authoritative** concept edge set,
    replacing Tier-1.

    ``doc_id`` is validated as a filesystem-safe slug; ``section_id`` is validated by
    **membership** — it must be a real section of the document (its dotted
    ``<doc_id>.<slug>`` form is not a bare slug) — via :func:`get_section`, which also
    rejects traversal values because they match no section.
    """

    resolved_doc_id = _validate_id(doc_id, "doc_id")
    # Membership check only (result discarded): raises SectionNotFoundError for an unknown
    # or traversal id. include_assets=False skips the document.json read + asset resolution
    # this call has no use for.
    get_section(workspace, resolved_doc_id, section_id, include_assets=False)

    # None (concepts omitted) is passed through as "no concept opinion → keep the
    # section's auto concepts"; an explicit list — including [] — is the agent taking
    # over the section (empty = deliberate prune). Only a summary-only call omits it.
    inputs = (
        None
        if concepts is None
        else [
            AnnotatedConcept(slug=concept.slug, title=concept.title, gloss=concept.gloss)
            for concept in concepts
        ]
    )
    try:
        annotation = build_annotation(
            resolved_doc_id,
            section_id,
            inputs,
            summary=summary,
            model=model,
            created_at=datetime.now(UTC).isoformat(),
        )
    except ValueError as exc:
        raise ReadingServiceError(str(exc)) from exc

    path = write_annotation(
        annotation, annotation_path(workspace.annotations_root, resolved_doc_id, section_id)
    )
    return AnnotationResult(
        doc_id=annotation.doc_id,
        section_id=annotation.section_id,
        concept_count=len(annotation.concepts or []),
        path=str(path),
    )
