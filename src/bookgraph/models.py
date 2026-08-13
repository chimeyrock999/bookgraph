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


class CanonicalBlock(BaseModel):
    """Parser-independent content block consumed by segmenters."""

    id: str
    type: BlockType
    text: str = ""
    level: int | None = None
    page_idx: int | None = None
    bbox: tuple[float, float, float, float] | None = None
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

    The authoritative concept edge set for its section (see
    ``.docs/cli/annotations.md``): on the next ``index build`` it overrides the
    deterministic Tier-1 extraction, and an empty ``concepts`` list prunes that
    section's concept mentions. ``summary`` is the agent's explanation of the section,
    surfaced immediately by the MCP ``get_context`` tool.
    """

    doc_id: str
    section_id: str
    concepts: list[AnnotatedConcept] = Field(default_factory=list)
    summary: str = ""
    model: str | None = None
    created_at: str | None = None
