from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

BlockType = Literal[
    "title",
    "text",
    "list",
    "table",
    "image",
    "chart",
    "equation",
    "unknown",
]

# Block types that carry an extracted image/table asset file (``CanonicalBlock.asset_path``)
# and are surfaced as structured ``AssetRef``s by the MCP section APIs. Shared by the parser
# (which attaches the path) and the service (which resolves it) so the two never drift.
ASSET_BLOCK_TYPES: frozenset[str] = frozenset({"image", "table", "chart"})


class CanonicalBlock(BaseModel):
    """Parser-independent content block consumed by segmenters."""

    id: str
    type: BlockType
    text: str = ""
    level: int | None = None
    page_idx: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    asset_path: str | None = None
    source_path: str | None = None
    order: int | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class Document(BaseModel):
    doc_id: str
    title: str
    blocks: list[CanonicalBlock] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class Section(BaseModel):
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
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ReadingPlan(BaseModel):
    """Persisted daily reading progression state for one document."""

    plan_id: str
    doc_id: str
    daily_sections: int = 1
    section_ids: list[str] = Field(default_factory=list)
    completed: list[str] = Field(default_factory=list)


class AnnotatedConcept(BaseModel):
    """One agent-identified concept edge within a section annotation.

    ``gloss`` is a short per-section note on why the concept matters here; it may be
    empty. ``slug`` is the cross-book join key (derived by slugifying ``title`` when a
    caller does not supply one).
    """

    slug: str
    title: str
    gloss: str = ""


class SectionAnnotation(BaseModel):
    """A reading agent's Tier-2 annotation of one section.

    ``concepts`` has three states that the merge (see ``docs/cli/annotations.md``)
    treats distinctly on the next ``index build``:

    - ``None`` — the agent expressed **no opinion** on concepts (e.g. a summary-only
      annotation); the section keeps its deterministic Tier-1 concepts.
    - ``[]`` — a **deliberate prune**: the section's concept mentions are zeroed out
      (the tokenizer-false-positive fix).
    - a non-empty list — the **authoritative** concept edge set, replacing Tier-1.

    ``summary`` is the agent's explanation of the section, surfaced immediately by the
    MCP ``get_context`` tool.
    """

    doc_id: str
    section_id: str
    concepts: list[AnnotatedConcept] | None = None
    summary: str = ""
    model: str | None = None
    created_at: str | None = None


# Kinds of generated per-section artifact the registry tracks. Only translations exist
# today; a new kind is a new literal plus its storage root in ``bookgraph.translations``.
SectionArtifactType = Literal["translation"]


class TranslationUnit(BaseModel):
    """One translated unit as a writer submits it: Markdown plus the blocks it translates.

    ``source_block_ids`` are ids of the section's ``CanonicalBlock``s, in reading order.
    Several ids merge paragraphs into one unit (n:1); consecutive units that repeat a
    block split it (1:n).
    """

    source_block_ids: list[str]
    content: str


class AlignedUnit(BaseModel):
    """One unit of a block-aligned translation as the registry stores it.

    ``start``/``end`` are the unit's character offsets in the decoded body
    (``body[start:end]``); the units appear in body order and source order.
    """

    source_block_ids: list[str]
    start: int
    end: int


class SectionArtifact(BaseModel):
    """Registry metadata for one generated per-section artifact (e.g. a translation).

    Stored as a JSON sidecar next to the artifact body. ``path`` is the body's location
    relative to the workspace root. ``source_section_hash`` is the
    :func:`bookgraph.translations.section_content_hash` of the section the artifact was
    generated from: when the section's current hash differs, the artifact is stale.
    ``content_hash`` fingerprints the body itself, so a body overwritten after
    registration is no longer vouched for by this record.
    ``includes_assets`` records whether the section's figures/tables were carried into
    the artifact (a translation of the prose alone is incomplete for an asset section).
    ``notes`` is the writer's free-text side channel — QA/checker results, terminology
    decisions, job remarks. It lives only here, never in the body, so a translator has
    a place for everything that is not book content.
    ``alignment`` (optional) maps the body back to the source blocks it translates, unit
    by unit (see :class:`AlignedUnit`); ``None`` is an unaligned translation, which is
    still valid. It is provenance, not content: neither hash covers it.
    """

    type: SectionArtifactType = "translation"
    lang: str
    doc_id: str
    section_id: str
    path: str
    source_section_hash: str
    content_hash: str
    includes_assets: bool = False
    model: str | None = None
    created_at: str | None = None
    notes: str | None = None
    alignment: list[AlignedUnit] | None = None


# What a translation must carry over unchanged from its source section (see
# ``bookgraph.translation_structure``): link and image destinations, reference-style
# link definitions, HTML ``id``/``name`` anchors, and explicit heading ids (``{#id}``).
StructuralTargetKind = Literal["link", "image", "reference", "html_id", "heading_id"]


class TranslationStructureIssue(BaseModel):
    """One structural target a translation changed relative to its source section.

    ``missing`` means the source has ``target`` (``count`` more times) than the
    translation; ``added`` means the translation has it and the source does not. A
    rewritten destination shows up as one ``missing`` plus one ``added`` entry.
    """

    kind: StructuralTargetKind
    target: str
    change: Literal["missing", "added"]
    count: int = 1


class MissingTranslationAsset(BaseModel):
    """A staged figure/table of a section that its translation body does not link.

    ``link`` is the asset's ``AssetRef.link`` — the relative reference the translation
    should carry (``![caption](<link>)``); ``block_id`` is the parsed block it belongs
    to and ``reference`` the parser's raw asset reference.
    """

    block_id: str
    type: str
    link: str
    reference: str
    caption: str = ""


# Whether a translation's body is mapped back to its source blocks: ``aligned`` (a valid
# alignment), ``unaligned`` (none recorded), ``invalid`` (recorded, but it no longer fits
# the section's blocks or the body — read as unaligned).
AlignmentStatus = Literal["aligned", "unaligned", "invalid"]

# ``unaligned_block`` is a warning (a source text block no unit translates); the others
# make an alignment invalid, and are refused on write.
AlignmentIssueCode = Literal[
    "unaligned_block", "empty_unit", "foreign_block", "out_of_order", "bad_range"
]


class TranslationAlignmentIssue(BaseModel):
    """One problem with a translation's block alignment.

    ``unit`` is the unit's index (``None`` for a gap); ``block_id`` the source block
    concerned, when there is one.
    """

    code: AlignmentIssueCode
    message: str
    unit: int | None = None
    block_id: str | None = None
