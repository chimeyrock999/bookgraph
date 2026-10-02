"""Pure reading/query logic behind the BookGraph MCP tools.

These functions operate on a :class:`WorkspacePaths` and the on-disk artifacts
written by the segment and reading-plan stages. They have no FastMCP dependency
so they can be unit-tested directly; the MCP server is a thin wrapper in
:mod:`bookgraph.mcp.server`.
"""

from __future__ import annotations

import json
import threading
from collections import Counter, OrderedDict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from bookgraph.annotations import (
    annotation_path,
    build_annotation,
    read_annotation,
    write_annotation,
)
from bookgraph.artifact_hygiene import ArtifactHygieneError
from bookgraph.assets import asset_reference, resolve_asset_path
from bookgraph.concept_hygiene import (
    DEFAULT_MERGE_THRESHOLD,
    LintFinding,
    MergeSuggestion,
    ReviewItem,
    lint_concepts,
    review_queue,
    suggest_merges,
)
from bookgraph.concept_registry import ConceptRegistry, read_registry
from bookgraph.documents import read_document
from bookgraph.graph import SectionGraph, SectionNode, build_section_graph, chapter_span
from bookgraph.index import ConceptMention, default_index_backend, tokenize
from bookgraph.models import (
    ASSET_BLOCK_TYPES,
    AnnotatedConcept,
    CanonicalBlock,
    ReadingPlan,
    Section,
)
from bookgraph.quality import (
    AssetSummary,
    SectionWarning,
    classify_asset,
    section_warnings,
)
from bookgraph.reading_plans import (
    ChapterProgress,
    chapter_progress,
    create_reading_plan,
    list_plan_progress,
    mark_section_read,
    next_sections,
    plan_lock,
    read_reading_plan,
    write_reading_plan,
)
from bookgraph.sections import count_sections, read_sections
from bookgraph.translations import (
    TranslationState,
    TranslationStatus,
    iter_translation_keys,
    section_content_hash,
    translation_state,
    validate_lang,
    write_translation,
)
from bookgraph.utils import ID_PATTERN, validate_slug_id
from bookgraph.workspace import WorkspacePaths


class ReadingServiceError(Exception):
    """Base class for expected, client-facing reading-service failures."""


class InvalidIdError(ReadingServiceError):
    """A client-supplied id is not a filesystem-safe slug."""


class PlanNotFoundError(ReadingServiceError):
    """A requested reading plan does not exist."""


class SectionsNotFoundError(ReadingServiceError):
    """A document has no sections manifest (it has not been segmented)."""


class SectionNotFoundError(ReadingServiceError):
    """A requested section id does not exist in a document."""


class ConceptNotFoundError(ReadingServiceError):
    """A requested concept slug is not present in the index."""


class AssetRef(BaseModel):
    """A figure/table asset that belongs to a section, resolved to a real file.

    ``caption`` is the block's text (a MinerU image/table block surfaces only its
    caption as text — the labels/data live inside ``path``). ``order`` is the block's
    position in the parsed document, so a client can place the asset relative to the
    section's prose.

    ``type`` is the parser's classification, kept verbatim. Layout parsers do confuse
    figures with tables, so it travels with ``type_confidence`` (how well the caption's
    label corroborates it) and, when the caption contradicts the parser,
    ``suggested_type`` — the type the caption implies. A client can then trust, correct,
    or open the file instead of taking a silently wrong ``type`` at face value.
    """

    block_id: str
    type: str
    path: str
    caption: str = ""
    order: int | None = None
    page_idx: int | None = None
    type_confidence: float = 1.0
    suggested_type: str | None = None


class SectionView(BaseModel):
    """A section's full reading content plus provenance and its Markdown path.

    ``warnings`` carries the section's data-quality anomalies (see
    :mod:`bookgraph.quality`) — a broken page span, a disputed asset type, prose that
    is only asset captions — so a reader sees them inline instead of having to inspect
    ``sources/parsed/<doc_id>/document.json``. Page-range warnings are always present;
    asset warnings need ``include_assets`` (the default).
    """

    id: str
    doc_id: str
    title: str
    level: int
    heading_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    text: str
    prev_id: str | None = None
    next_id: str | None = None
    block_ids: list[str] = Field(default_factory=list)
    markdown_path: str
    assets: list[AssetRef] = Field(default_factory=list)
    warnings: list[SectionWarning] = Field(default_factory=list)


class NextSection(BaseModel):
    """The next reading tick: the unread sections a reader should tackle next."""

    plan_id: str
    doc_id: str
    sections: list[SectionView]
    remaining: int
    done: bool
    chapter: ChapterProgressView | None = None


class MarkReadResult(BaseModel):
    """Outcome of marking a section read."""

    plan_id: str
    marked: str
    completed: int
    total: int
    done: bool


class SearchHit(BaseModel):
    """One section matched by :func:`search_sections`."""

    section_id: str
    doc_id: str
    title: str
    score: float
    snippet: str


class SearchResult(BaseModel):
    """Ranked search hits for a query."""

    query: str
    hits: list[SearchHit]


class SectionRef(BaseModel):
    """A lightweight pointer to a section (no body text)."""

    id: str
    title: str
    level: int


class ChapterProgressView(BaseModel):
    """Progress through the chapter holding a plan's next unread section.

    ``section`` is the chapter (scope) heading; ``completed``/``remaining``/``total``
    count the plan's sections in that chapter's subtree; ``next_boundary`` is the first
    section after the chapter (``None`` at the end of the document).
    """

    section: SectionRef
    completed: int
    remaining: int
    total: int
    next_boundary: SectionRef | None = None


class PlanProgress(BaseModel):
    """A reading plan's overall and current-chapter progress, without section bodies."""

    plan_id: str
    doc_id: str
    completed: int
    total: int
    remaining: int
    done: bool
    current_section_id: str | None = None
    chapter: ChapterProgressView | None = None
    next_sections: list[SectionRef] = Field(default_factory=list)


class OutlineNode(BaseModel):
    """One entry in a document's outline: a section and its hierarchy links."""

    id: str
    title: str
    level: int
    parent_id: str | None = None
    child_ids: list[str] = Field(default_factory=list)


class Outline(BaseModel):
    """A document's section outline in reading order, optionally scoped.

    ``root_id`` is the subtree the outline was scoped to (``None`` = the whole
    document); ``total_nodes`` is the document's full section count. ``truncated`` is
    true when ``max_depth`` cut off deeper sections in scope — their ids still appear in
    the boundary nodes' ``child_ids``, so a client can drill in with ``root_id``.
    """

    doc_id: str
    nodes: list[OutlineNode]
    root_id: str | None = None
    total_nodes: int = 0
    truncated: bool = False


class SectionTree(BaseModel):
    """A small outline around one section: its breadcrumb, siblings, and children.

    ``ancestors`` run root-first (top-level section down to the parent). ``siblings``
    are the parent's children in reading order — the section itself included, so its
    position among them is visible; for a top-level section they are the top-level
    sections. A flat document (e.g. page/token fallback, every section top-level) would
    make that the whole book, so siblings are windowed around the section and
    ``siblings_truncated`` says whether any were dropped.
    """

    doc_id: str
    section: SectionRef
    ancestors: list[SectionRef] = Field(default_factory=list)
    siblings: list[SectionRef] = Field(default_factory=list)
    siblings_truncated: bool = False
    children: list[SectionRef] = Field(default_factory=list)


class ProgressNode(OutlineNode):
    """An outline node annotated with whether a reading plan has read it."""

    read: bool = False


class ChapterOutline(BaseModel):
    """The outline of the chapter a reading plan is currently in.

    The chapter is the scope ancestor of ``current_section_id`` (the plan's next unread
    section) — see :func:`bookgraph.graph.chapter_span`. ``nodes`` and ``completed`` /
    ``remaining`` / ``total`` cover the chapter's span, by membership (counts unaffected
    by ``max_depth``): normally its whole subtree, but only the heading's own section
    while a wrapper heading (a lone book-title root, or with ``chapter_level`` any
    shallower heading such as a part) is itself being read. ``plan_completed`` /
    ``plan_total`` are plan-wide. When the plan is ``done`` there is no current section,
    so ``chapter`` is ``None``, ``nodes`` is empty, and the chapter counts are zero.
    """

    plan_id: str
    doc_id: str
    current_section_id: str | None = None
    chapter: SectionRef | None = None
    nodes: list[ProgressNode] = Field(default_factory=list)
    truncated: bool = False
    completed: int = 0
    remaining: int = 0
    total: int = 0
    plan_completed: int
    plan_total: int
    done: bool


class RelatedSections(BaseModel):
    """A section's structural neighbours in the document graph."""

    doc_id: str
    section_id: str
    parent: SectionRef | None = None
    prev: SectionRef | None = None
    next: SectionRef | None = None
    children: list[SectionRef] = Field(default_factory=list)


class ConceptRef(BaseModel):
    """A lightweight concept pointer with its cross-book reach (no backlinks).

    ``gloss`` / ``source`` describe this concept **in the current section**: the gloss
    an agent attached to the mention here, and whether the mention is ``"auto"`` (Tier-1)
    or ``"agent"`` (Tier-2). They are section-scoped, unlike ``doc_count`` /
    ``mention_count`` which are the concept's cross-book totals.
    """

    slug: str
    title: str
    doc_count: int
    mention_count: int
    gloss: str = ""
    source: str = "auto"


class SectionContext(BaseModel):
    """A section's full content, its graph neighbourhood, its concepts, and summary.

    ``summary`` is the Tier-2 agent annotation for this section, read directly from the
    annotation file so it reflects an ``annotate_section`` call immediately (before any
    ``index build``). Empty when the section has not been annotated.
    """

    section: SectionView
    related: RelatedSections
    concepts: list[ConceptRef] = Field(default_factory=list)
    summary: str = ""


class ConceptMentionView(BaseModel):
    """One backlink: a section (in some book) that mentions a concept.

    ``summary`` is the mentioning section's Tier-2 annotation summary — the long-form
    context behind the backlink. It is populated only in the concept-detail view
    (``get_concept(..., include_annotations=True)``); the compact card leaves it empty
    to stay lightweight for graph traversal.
    """

    doc_id: str
    section_id: str
    title: str
    gloss: str = ""
    source: str = "auto"
    summary: str = ""
    raw_slug: str = ""


class ConceptInput(BaseModel):
    """One concept an agent asserts for a section (input to ``annotate_section``).

    ``slug`` is optional — it is derived from ``title`` when omitted; a title/slug that
    slugifies to empty or ``untitled`` is rejected. ``gloss`` is an optional per-section
    note on why the concept matters here.
    """

    title: str
    slug: str = ""
    gloss: str = ""


class AnnotationResult(BaseModel):
    """Outcome of writing a Tier-2 section annotation."""

    doc_id: str
    section_id: str
    concept_count: int
    path: str


class ConceptView(BaseModel):
    """A concept aggregated across books, with its cross-book backlinks.

    Two read modes share this shape. The default is a **compact card** for lightweight
    graph traversal: title, cross-book totals, and bare backlink pointers (glosses only).
    The **detail view** (``get_concept(..., include_annotations=True)``) additionally
    fills each mention's ``summary`` with the section's Tier-2 annotation, so a concept
    with several mentions renders as a readable, provenance-aware note rather than a set
    of short glosses. ``annotated_mention_count`` reports how many mentions carry such a
    summary — meaningful in **both** modes (the compact card leaves the summaries empty
    but still counts them), so a cheap card read tells an agent whether a concept has
    deeper context worth an ``include_annotations=True`` call.

    ``slug`` / ``title`` are the **canonical** concept. ``aliases`` lists the slugs that
    resolve to it — the registry's deprecated aliases plus any alias slug observed on a
    mention (``ConceptMentionView.raw_slug``). ``canonical`` is true when a reviewer
    marked the concept canonical in ``concepts/registry.json``; ``resolved_from`` is the
    requested slug when it was an alias that resolved here (``None`` otherwise).
    """

    slug: str
    title: str
    doc_count: int
    mention_count: int
    annotated_mention_count: int = 0
    aliases: list[str] = Field(default_factory=list)
    canonical: bool = False
    resolved_from: str | None = None
    mentions: list[ConceptMentionView] = Field(default_factory=list)


class ConceptHygieneReport(BaseModel):
    """Concept-maintenance signals over the built graph (see ``concept_hygiene``).

    ``merge_suggestions`` are likely duplicates (best first), ``lint`` flags concepts
    that probably should not be durable, and ``review_queue`` lists agent-created
    concepts no reviewer has accepted, aliased, or ignored yet. Act on them with the
    ``bookgraph concepts`` CLI, then re-run ``bookgraph index build``.
    """

    concept_count: int
    merge_suggestions: list[MergeSuggestion] = Field(default_factory=list)
    lint: list[LintFinding] = Field(default_factory=list)
    review_queue: list[ReviewItem] = Field(default_factory=list)


def _section_markdown_path(workspace: WorkspacePaths, doc_id: str, section_id: str) -> Path:
    return workspace.sources_sections / doc_id / f"{section_id}.md"


# Parsed document.json blocks cached so an agent calling get_section in a loop does not
# re-read and re-validate the whole document on every call. Keyed by (mtime_ns, size): the
# nanosecond mtime plus byte size invalidates on any realistic re-parse (a same-tick rewrite
# to the exact same byte length is the one theoretical gap, only on coarse-mtime filesystems).
# Bounded by an LRU cap, and guarded by a lock so concurrent MCP requests keep the bound and
# recency order consistent.
_DocBlocks = dict[str, CanonicalBlock]
_DOC_BLOCKS_CACHE: OrderedDict[Path, tuple[int, int, _DocBlocks]] = OrderedDict()
_DOC_BLOCKS_CACHE_MAX = 32
_DOC_BLOCKS_LOCK = threading.Lock()


def _load_doc_blocks(workspace: WorkspacePaths, doc_id: str) -> _DocBlocks:
    """Index the parsed ``document.json`` blocks by id, or ``{}`` when unavailable.

    ``Section.block_ids`` is the only link back to the parser's richer blocks (where the
    image/table asset paths live). A document that was never parsed to ``document.json``
    (e.g. a fixture that only writes ``sections.jsonl``) simply yields no assets. Results
    are memoised by (mtime_ns, size) to keep per-section fetches O(1) across calls.
    """

    document_path = workspace.sources_parsed / doc_id / "document.json"
    try:
        stat = document_path.stat()
    except OSError:
        with _DOC_BLOCKS_LOCK:
            _DOC_BLOCKS_CACHE.pop(document_path, None)
        return {}
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _DOC_BLOCKS_LOCK:
        cached = _DOC_BLOCKS_CACHE.get(document_path)
        if cached is not None and (cached[0], cached[1]) == stamp:
            _DOC_BLOCKS_CACHE.move_to_end(document_path)
            return cached[2]
    # Read/validate outside the lock (the expensive part); a concurrent miss just re-reads.
    try:
        document = read_document(document_path)
    except (OSError, ValueError):
        return {}
    blocks = {block.id: block for block in document.blocks}
    with _DOC_BLOCKS_LOCK:
        _DOC_BLOCKS_CACHE[document_path] = (stamp[0], stamp[1], blocks)
        _DOC_BLOCKS_CACHE.move_to_end(document_path)
        while len(_DOC_BLOCKS_CACHE) > _DOC_BLOCKS_CACHE_MAX:
            _DOC_BLOCKS_CACHE.popitem(last=False)
    return blocks


def _section_assets(
    workspace: WorkspacePaths,
    section: Section,
    blocks_by_id: dict[str, CanonicalBlock],
) -> tuple[list[AssetRef], list[AssetSummary]]:
    """Resolve a section's asset blocks into openable ``AssetRef``s.

    Returns the assets a client can open plus a summary of **every** asset block of the
    section, including the ones whose file is missing — those are dropped from ``assets``
    (an ``AssetRef.path`` must always open) but still feed the quality checks, so a
    reference the parser never staged is reported rather than silently disappearing.
    """

    assets: list[AssetRef] = []
    summaries: list[AssetSummary] = []
    parsed_dir = workspace.sources_parsed / section.doc_id
    for block_id in section.block_ids:
        block = blocks_by_id.get(block_id)
        if block is None or block.type not in ASSET_BLOCK_TYPES:
            continue
        if not asset_reference(block):
            # An asset-typed block with no file reference at all (e.g. a markdown table
            # rendered inline into ``text``) is content, not a missing asset — skip it.
            continue
        path = resolve_asset_path(parsed_dir, block)
        summaries.append(
            AssetSummary(
                block_id=block.id,
                type=block.type,
                caption=block.text,
                resolved=path is not None,
            )
        )
        if path is None:
            continue
        classification = classify_asset(block.type, block.text)
        assets.append(
            AssetRef(
                block_id=block.id,
                type=block.type,
                path=path,
                caption=block.text,
                order=block.order,
                page_idx=block.page_idx,
                type_confidence=classification.confidence,
                suggested_type=classification.suggested_type,
            )
        )
    return assets, summaries


def _section_view(
    workspace: WorkspacePaths,
    section: Section,
    *,
    include_assets: bool = True,
    blocks_by_id: dict[str, CanonicalBlock] | None = None,
) -> SectionView:
    markdown_path = _section_markdown_path(workspace, section.doc_id, section.id)
    assets: list[AssetRef] = []
    asset_summaries: list[AssetSummary] = []
    if include_assets:
        if blocks_by_id is None:
            blocks_by_id = _load_doc_blocks(workspace, section.doc_id)
        assets, asset_summaries = _section_assets(workspace, section, blocks_by_id)
    return SectionView(
        id=section.id,
        doc_id=section.doc_id,
        title=section.title,
        level=section.level,
        heading_path=section.heading_path,
        page_start=section.page_start,
        page_end=section.page_end,
        text=section.text,
        prev_id=section.prev_id,
        next_id=section.next_id,
        block_ids=section.block_ids,
        markdown_path=str(markdown_path),
        assets=assets,
        # The same checks back the segment stage's quality.json report, so a reader and
        # an ingest run never disagree about what is wrong with a section.
        warnings=section_warnings(section, asset_summaries),
    )


def _validate_id(value: str, field_name: str) -> str:
    """Reject client-supplied ids that are not filesystem-safe slugs.

    MCP tool inputs are client-controlled, so any id that becomes a path
    component (``plan_id``, ``doc_id``) must be validated before it is joined onto
    a workspace path — otherwise a value like ``../secret`` could escape the
    intended artifact directory.
    """

    try:
        return validate_slug_id(value, field_name=field_name)
    except ValueError as exc:
        raise InvalidIdError(str(exc)) from exc


def _load_doc_sections(workspace: WorkspacePaths, doc_id: str) -> list[Section]:
    _validate_id(doc_id, "doc_id")
    manifest = workspace.sources_sections / doc_id / "sections.jsonl"
    if not manifest.is_file():
        raise SectionsNotFoundError(
            f"No sections for '{doc_id}': {manifest} not found. Run 'bookgraph segment' first."
        )
    try:
        return read_sections(manifest)
    except (OSError, ValueError) as exc:
        raise SectionsNotFoundError(f"Invalid sections manifest: {manifest}: {exc}") from exc


def _plan_path(workspace: WorkspacePaths, plan_id: str) -> Path:
    return workspace.reading_plans_root / f"{_validate_id(plan_id, 'plan_id')}.json"


def _find_section(workspace: WorkspacePaths, doc_id: str, section_id: str) -> Section:
    """Look up a section by membership (rejects unknown and traversal ids)."""

    for section in _load_doc_sections(workspace, doc_id):
        if section.id == section_id:
            return section
    raise SectionNotFoundError(f"Section '{section_id}' not found in document '{doc_id}'.")


def _load_plan(workspace: WorkspacePaths, plan_id: str) -> tuple[Path, ReadingPlan]:
    path = _plan_path(workspace, plan_id)
    if not path.is_file():
        raise PlanNotFoundError(
            f"Reading plan '{plan_id}' not found: {path}. "
            "Run 'bookgraph reading-plan create' first."
        )
    try:
        return path, read_reading_plan(path)
    except (OSError, ValueError) as exc:
        raise PlanNotFoundError(f"Invalid reading plan: {path}: {exc}") from exc


def _chapter_progress(
    plan: ReadingPlan, sections: list[Section], chapter_level: int | None
) -> ChapterProgress:
    if chapter_level is not None and chapter_level < 1:
        raise ReadingServiceError("chapter_level must be at least 1")
    try:
        return chapter_progress(plan, sections, chapter_level=chapter_level)
    except ValueError as exc:  # the plan's next section is missing from the manifest
        raise SectionNotFoundError(str(exc)) from exc


def _chapter_view(
    progress: ChapterProgress, by_id: dict[str, Section]
) -> ChapterProgressView | None:
    if progress.chapter_id is None:
        return None
    boundary = (
        _section_ref(by_id[progress.next_boundary_id])
        if progress.next_boundary_id is not None
        else None
    )
    return ChapterProgressView(
        section=_section_ref(by_id[progress.chapter_id]),
        completed=progress.completed_in_chapter,
        remaining=progress.remaining_in_chapter,
        total=progress.total_in_chapter,
        next_boundary=boundary,
    )


def _section_ref(section: Section | SectionNode) -> SectionRef:
    return SectionRef(id=section.id, title=section.title, level=section.level)


def _current_batch(
    plan: ReadingPlan,
    sections: list[Section],
    *,
    stop_at_boundary: bool = False,
    chapter_level: int | None = None,
    progress: ChapterProgress | None = None,
) -> list[str]:
    """The plan's current batch: the one resolver behind ``get_next_section`` and the
    reading-batch tools, so the batch an agent was handed is the batch it completes.

    Without ``stop_at_boundary`` it is the next ``daily_sections`` unread sections; with
    it, that batch clipped at the end of the current chapter (``chapter_level`` picks
    the chapter's heading level). Pass an already computed ``progress`` to reuse it.
    """

    if not stop_at_boundary:
        return next_sections(plan).sections
    if progress is None:
        progress = _chapter_progress(plan, sections, chapter_level)
    return progress.next_section_ids


def get_next_section(
    workspace: WorkspacePaths,
    plan_id: str,
    include_assets: bool = True,
    *,
    stop_at_boundary: bool = False,
    chapter_level: int | None = None,
) -> NextSection:
    """Return the next unread sections for a plan, with full content.

    ``include_assets`` (default true) mirrors ``get_section`` — set it false to skip the
    figure/table resolution (and its ``document.json`` read) on plans read for prose only.
    The result carries ``chapter`` progress (see :func:`get_plan_progress`); with
    ``stop_at_boundary`` the batch is clipped at the end of that chapter, so a tick never
    spills into the next one. ``chapter_level`` picks the heading level of the chapter
    scope (default: the top-level ancestor, skipping a lone book-title root).
    """

    _, plan = _load_plan(workspace, plan_id)
    pack = next_sections(plan)
    sections = _load_doc_sections(workspace, plan.doc_id)
    by_id = {section.id: section for section in sections}
    for section_id in pack.sections:
        if section_id not in by_id:
            raise SectionNotFoundError(
                f"Reading plan '{plan_id}' references unknown section '{section_id}' "
                f"in document '{plan.doc_id}'."
            )
    progress = _chapter_progress(plan, sections, chapter_level)
    batch = _current_batch(plan, sections, stop_at_boundary=stop_at_boundary, progress=progress)
    blocks_by_id = _load_doc_blocks(workspace, plan.doc_id) if include_assets else None
    views = [
        _section_view(
            workspace, by_id[section_id], include_assets=include_assets, blocks_by_id=blocks_by_id
        )
        for section_id in batch
    ]
    return NextSection(
        plan_id=plan.plan_id,
        doc_id=plan.doc_id,
        sections=views,
        remaining=pack.remaining,
        done=pack.done,
        chapter=_chapter_view(progress, by_id),
    )


def get_plan_progress(
    workspace: WorkspacePaths, plan_id: str, chapter_level: int | None = None
) -> PlanProgress:
    """Report a plan's progress overall and within its current chapter.

    Answers "how many sections until the end of this chapter" without fetching section
    bodies or the full outline: the chapter is the scope ancestor of the next unread
    section (resolved by :func:`~bookgraph.graph.resolve_chapter`; ``chapter_level`` picks
    its heading level), counts are by membership so resets and skipped/out-of-order reads stay
    correct, and ``next_sections`` is the next ``daily_sections`` batch clipped at the
    chapter boundary.
    """

    _, plan = _load_plan(workspace, plan_id)
    sections = _load_doc_sections(workspace, plan.doc_id)
    by_id = {section.id: section for section in sections}
    progress = _chapter_progress(plan, sections, chapter_level)
    return PlanProgress(
        plan_id=plan.plan_id,
        doc_id=plan.doc_id,
        completed=len(plan.section_ids) - progress.remaining,
        total=len(plan.section_ids),
        remaining=progress.remaining,
        done=progress.done,
        current_section_id=progress.current_section_id,
        chapter=_chapter_view(progress, by_id),
        next_sections=[_section_ref(by_id[section_id]) for section_id in progress.next_section_ids],
    )


def get_section(
    workspace: WorkspacePaths,
    doc_id: str,
    section_id: str,
    include_assets: bool = True,
) -> SectionView:
    """Return one section's full reading content by id.

    When ``include_assets`` (the default) the view carries a structured ``assets`` list
    of the section's figures/tables (path, type, caption, order) so a reader no longer has
    to grep the parsed ``document.json`` to find them.
    """

    section = _find_section(workspace, doc_id, section_id)
    return _section_view(workspace, section, include_assets=include_assets)


def mark_read(
    workspace: WorkspacePaths, plan_id: str, section_id: str | None = None
) -> MarkReadResult:
    """Mark a section read for a plan and persist the updated plan."""

    path = _plan_path(workspace, plan_id)
    with plan_lock(path):
        _, plan = _load_plan(workspace, plan_id)
        try:
            updated, marked = mark_section_read(plan, section_id)
        except ValueError as exc:
            raise ReadingServiceError(str(exc)) from exc
        write_reading_plan(updated, path)
    return MarkReadResult(
        plan_id=updated.plan_id,
        marked=marked,
        completed=len(updated.completed),
        total=len(updated.section_ids),
        done=len(updated.completed) == len(updated.section_ids),
    )


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


def _segmented_doc_ids(workspace: WorkspacePaths) -> list[str]:
    """Slug-shaped document directories that have a sections manifest.

    Enumerated directory names are workspace-internal, not client input, but only
    slug-shaped ones can have been produced by ``segment``; skipping the rest keeps
    the per-doc id validation in ``_load_doc_sections`` from aborting a
    workspace-wide search on a stray directory.
    """

    root = workspace.sources_sections
    return sorted(
        child.name
        for child in (root.iterdir() if root.is_dir() else [])
        if (child / "sections.jsonl").is_file() and ID_PATTERN.fullmatch(child.name)
    )


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
        raise SectionNotFoundError(
            f"Section '{root_id}' not found in document '{graph.doc_id}'."
        )

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


# --- Section artifact registry (translations) ------------------------------------
#
# A reading job that translates sections caches each result under
# ``translations/<lang>/<doc_id>/<section_id>.md`` with a registry sidecar recording the
# section content hash it was made from, so a workflow can tell a reusable translation
# from a stale one without re-deriving it from path conventions. Pure storage logic
# lives in :mod:`bookgraph.translations`.


class SectionArtifactView(BaseModel):
    """A section translation's registry entry and freshness against the live section.

    ``status`` is ``fresh`` (reusable as-is), ``stale`` (the section changed since it was
    translated), ``untracked`` (a body with no registry sidecar — freshness unknown),
    ``missing`` (no translation), or ``orphaned`` (its section no longer exists; listing
    only). ``current_section_hash`` is the section's live content hash — pass it back as
    ``source_section_hash`` to ``write_section_translation`` to pin the write to the
    content you translated. ``section_has_assets`` together with ``includes_assets``
    tells whether a translation is complete: reuse it as-is only when ``status`` is
    ``fresh`` **and** (``includes_assets`` or not ``section_has_assets``). A translation
    that left out the section's figures/tables is prose-only even when fresh, because
    the section hash covers only its title and text.
    ``content`` is the translation body when requested and present.
    """

    type: str = "translation"
    lang: str
    doc_id: str
    section_id: str
    status: TranslationStatus
    path: str | None = None
    metadata_path: str | None = None
    source_section_hash: str | None = None
    current_section_hash: str | None = None
    includes_assets: bool | None = None
    section_has_assets: bool = False
    model: str | None = None
    created_at: str | None = None
    content: str | None = None


class SectionArtifactList(BaseModel):
    """Registered section artifacts, each with its freshness status."""

    artifacts: list[SectionArtifactView] = Field(default_factory=list)


def _section_has_assets(workspace: WorkspacePaths, section: Section) -> bool:
    """Whether the section owns any figure/table asset block (staged or not)."""

    _, summaries = _section_assets(workspace, section, _load_doc_blocks(workspace, section.doc_id))
    return bool(summaries)


def _artifact_view(
    workspace: WorkspacePaths,
    state: TranslationState,
    section: Section | None,
    *,
    include_content: bool,
) -> SectionArtifactView:
    artifact = state.artifact
    body_exists = state.status != "missing"
    content: str | None = None
    if include_content and state.body is not None:
        # Decode the bytes the status was computed from (no second read of the file).
        content = state.body.decode("utf-8", errors="replace")
    return SectionArtifactView(
        lang=state.lang,
        doc_id=state.doc_id,
        section_id=state.section_id,
        status=state.status,
        path=str(state.paths.body) if body_exists else None,
        metadata_path=str(state.paths.metadata) if artifact is not None else None,
        source_section_hash=artifact.source_section_hash if artifact else None,
        current_section_hash=state.current_section_hash,
        includes_assets=artifact.includes_assets if artifact else None,
        section_has_assets=_section_has_assets(workspace, section) if section else False,
        model=artifact.model if artifact else None,
        created_at=artifact.created_at if artifact else None,
        content=content,
    )


def _validate_lang(lang: str) -> str:
    try:
        return validate_lang(lang)
    except ValueError as exc:
        raise InvalidIdError(str(exc)) from exc


def get_section_translation(
    workspace: WorkspacePaths,
    doc_id: str,
    section_id: str,
    lang: str,
    include_content: bool = True,
) -> SectionArtifactView:
    """Return a section's cached translation and whether it is still fresh.

    Never raises for a missing translation — ``status="missing"`` is the normal "go
    translate it" answer, and it still carries ``current_section_hash`` so the caller
    can pin its later write.
    """

    resolved_doc_id = _validate_id(doc_id, "doc_id")
    resolved_lang = _validate_lang(lang)
    section = _find_section(workspace, resolved_doc_id, section_id)
    state = translation_state(
        workspace, resolved_lang, resolved_doc_id, section.id, section_content_hash(section)
    )
    return _artifact_view(workspace, state, section, include_content=include_content)


def write_section_translation(
    workspace: WorkspacePaths,
    doc_id: str,
    section_id: str,
    lang: str,
    content: str,
    includes_assets: bool = False,
    model: str | None = None,
    source_section_hash: str | None = None,
) -> SectionArtifactView:
    """Cache a section translation and register it against the section's content.

    ``source_section_hash`` (optional) is the ``current_section_hash`` the caller saw
    when it fetched the section to translate; if the section has changed since, the
    write is refused so a translation of old content is never registered as fresh.
    ``includes_assets`` declares whether the section's figures/tables were carried into
    the translation.

    ``content`` must be the translated book content only: a write carrying a
    ``MEDIA:`` delivery marker, a cache/mark-read progress footer, a QA/checker note,
    an export status label, or an absolute asset link is refused (link parsed assets
    relatively; keep diagnostics in the reply or job log).
    """

    resolved_doc_id = _validate_id(doc_id, "doc_id")
    resolved_lang = _validate_lang(lang)
    if not content.strip():
        raise ReadingServiceError("translation content must not be empty")
    section = _find_section(workspace, resolved_doc_id, section_id)
    current_hash = section_content_hash(section)
    if source_section_hash is not None and source_section_hash != current_hash:
        raise ReadingServiceError(
            f"section '{section.id}' changed since it was translated "
            f"(translated {source_section_hash}, current {current_hash}); "
            "re-fetch it with get_section and translate the current content"
        )
    try:
        write_translation(
            workspace,
            section,
            resolved_lang,
            content,
            includes_assets=includes_assets,
            model=model,
            created_at=datetime.now(UTC).isoformat(),
        )
    except ArtifactHygieneError as exc:
        raise ReadingServiceError(str(exc)) from exc
    state = translation_state(workspace, resolved_lang, resolved_doc_id, section.id, current_hash)
    return _artifact_view(workspace, state, section, include_content=False)


def list_section_artifacts(
    workspace: WorkspacePaths,
    doc_id: str | None = None,
    lang: str | None = None,
    artifact_type: str = "translation",
) -> SectionArtifactList:
    """List cached section artifacts with their freshness (no bodies).

    Each entry is checked against the document's current sections, so a reading job can
    find stale translations to redo — and ``orphaned`` ones whose section is gone —
    in one call. Only ``artifact_type="translation"`` exists today.
    """

    if artifact_type != "translation":
        raise ReadingServiceError(
            f"unknown artifact type {artifact_type!r}; supported: 'translation'"
        )
    resolved_doc_id = _validate_id(doc_id, "doc_id") if doc_id is not None else None
    resolved_lang = _validate_lang(lang) if lang is not None else None

    sections_by_doc: dict[str, dict[str, Section]] = {}
    artifacts: list[SectionArtifactView] = []
    for key_lang, key_doc, key_section in iter_translation_keys(
        workspace, doc_id=resolved_doc_id, lang=resolved_lang
    ):
        if key_doc not in sections_by_doc:
            try:
                loaded = _load_doc_sections(workspace, key_doc)
            except ReadingServiceError:
                loaded = []  # document no longer segmented → its translations are orphaned
            sections_by_doc[key_doc] = {section.id: section for section in loaded}
        section = sections_by_doc[key_doc].get(key_section)
        state = translation_state(
            workspace,
            key_lang,
            key_doc,
            key_section,
            section_content_hash(section) if section else None,
        )
        artifacts.append(_artifact_view(workspace, state, section, include_content=False))
    return SectionArtifactList(artifacts=artifacts)


# --- Workspace orientation & reading-plan management -----------------------------
#
# Self-serve tools so a reading agent connected over MCP can discover what there is
# to read (``list_documents``) and start/track a reading plan (``create_plan`` /
# ``list_plans``) without a human running the CLI first. They reuse the same pure
# reading-plan logic as ``cli/reading_plan.py``.


class DocumentRef(BaseModel):
    """A segmented document available to read."""

    doc_id: str
    title: str
    section_count: int


class DocumentList(BaseModel):
    """The workspace's segmented documents."""

    documents: list[DocumentRef] = Field(default_factory=list)


class CreatedPlan(BaseModel):
    """Outcome of creating a reading plan."""

    plan_id: str
    doc_id: str
    daily_sections: int
    section_count: int
    path: str


class PlanRef(BaseModel):
    """A reading plan with its completion progress."""

    plan_id: str
    doc_id: str
    completed: int
    total: int
    done: bool


class PlanList(BaseModel):
    """The workspace's reading plans."""

    plans: list[PlanRef] = Field(default_factory=list)


def _document_title(workspace: WorkspacePaths, doc_id: str) -> str:
    """The parsed document's title, falling back to ``doc_id``."""

    path = workspace.sources_parsed / doc_id / "document.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return doc_id
    title = payload.get("title") if isinstance(payload, dict) else None
    return title if isinstance(title, str) and title else doc_id


def list_documents(workspace: WorkspacePaths) -> DocumentList:
    """List the workspace's segmented documents (what an agent can read)."""

    documents: list[DocumentRef] = []
    for doc_id in _segmented_doc_ids(workspace):
        manifest = workspace.sources_sections / doc_id / "sections.jsonl"
        try:
            section_count = count_sections(manifest)
        except OSError:
            continue  # a manifest that vanished/broke between listing and read
        documents.append(
            DocumentRef(
                doc_id=doc_id,
                title=_document_title(workspace, doc_id),
                section_count=section_count,
            )
        )
    return DocumentList(documents=documents)


def create_plan(
    workspace: WorkspacePaths,
    doc_id: str,
    plan_id: str | None = None,
    daily_sections: int = 1,
    *,
    overwrite: bool = False,
) -> CreatedPlan:
    """Create (and persist) a reading plan for a segmented document.

    ``plan_id`` defaults to ``doc_id``. To protect the autonomous agents this
    tool serves, creating a plan whose id already exists raises rather than
    silently discarding its progress; pass ``overwrite=True`` to replace it.
    """

    resolved_doc_id = _validate_id(doc_id, "doc_id")
    resolved_plan_id = _validate_id(plan_id or resolved_doc_id, "plan_id")
    if daily_sections < 1:
        raise ReadingServiceError("daily_sections must be at least 1")

    path = workspace.reading_plans_root / f"{resolved_plan_id}.json"
    with plan_lock(path):
        if path.exists() and not overwrite:
            raise ReadingServiceError(
                f"reading plan '{resolved_plan_id}' already exists; resume it with "
                "get_next_section/list_plans, or pass overwrite=True to replace it "
                "(discarding its progress)"
            )

        sections = _load_doc_sections(workspace, resolved_doc_id)
        try:
            plan = create_reading_plan(
                sections,
                plan_id=resolved_plan_id,
                doc_id=resolved_doc_id,
                daily_sections=daily_sections,
            )
        except ValueError as exc:
            raise ReadingServiceError(str(exc)) from exc

        write_reading_plan(plan, path)
    return CreatedPlan(
        plan_id=plan.plan_id,
        doc_id=plan.doc_id,
        daily_sections=plan.daily_sections,
        section_count=len(plan.section_ids),
        path=str(path),
    )


def list_plans(workspace: WorkspacePaths) -> PlanList:
    """List the workspace's reading plans with completion progress."""

    return PlanList(
        plans=[
            PlanRef(
                plan_id=progress.plan_id,
                doc_id=progress.doc_id,
                completed=progress.completed,
                total=progress.total,
                done=progress.done,
            )
            for progress in list_plan_progress(workspace.reading_plans_root)
        ]
    )
