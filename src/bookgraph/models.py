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
