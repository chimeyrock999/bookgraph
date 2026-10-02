"""Pydantic views returned by the MCP reading tools (:mod:`bookgraph.mcp.service`)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from bookgraph.concept_hygiene import (
    LintFinding,
    MergeSuggestion,
    ReviewItem,
)
from bookgraph.mcp.asset_views import AssetRef
from bookgraph.models import (
    TranslationStructureIssue,
)
from bookgraph.quality import (
    SectionWarning,
)
from bookgraph.translations import (
    TranslationStatus,
)


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
    ``structure_issues``: see :mod:`bookgraph.mcp.translation_structure`.
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
    notes: str | None = None
    content: str | None = None
    structure_issues: list[TranslationStructureIssue] = Field(default_factory=list)


class SectionArtifactList(BaseModel):
    """Registered section artifacts, each with its freshness status."""

    artifacts: list[SectionArtifactView] = Field(default_factory=list)


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
