from __future__ import annotations

import re
from dataclasses import dataclass

from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.ports import DocumentSegmenter

_TOKEN_RE = re.compile(r"\S+")
_TEXT_BLOCK_TYPES = {"title", "text", "list", "table", "equation", "unknown"}
_NON_TEXT_PROVENANCE_TYPES = {"image", "chart"}


@dataclass(frozen=True)
class _BlockGroup:
    blocks: list[CanonicalBlock]
    token_count: int


@dataclass(frozen=True)
class _Chunk:
    blocks: list[CanonicalBlock]
    token_count: int


@dataclass
class TokenPageSegmenter(DocumentSegmenter):
    """Fallback segmenter that chunks by token budget while respecting pages.

    It is intended for documents without useful headings or PDF bookmarks. Blocks
    are kept whole for provenance; an oversized block/page becomes its own
    section. When possible, the segmenter avoids crossing page boundaries if
    adding the next page would exceed the configured token budget.
    """

    max_tokens: int = 800
    name: str = "token-page"

    def segment(self, document: Document) -> list[Section]:
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")

        chunks = self._chunks(_page_groups(_segmentable_blocks(document.blocks)))
        sections = [
            self._to_section(document, index, chunk)
            for index, chunk in enumerate(chunks, 1)
        ]
        for index, section in enumerate(sections):
            section.prev_id = sections[index - 1].id if index > 0 else None
            section.next_id = sections[index + 1].id if index + 1 < len(sections) else None
        return sections

    def _chunks(self, groups: list[_BlockGroup]) -> list[_Chunk]:
        chunks: list[_Chunk] = []
        current_blocks: list[CanonicalBlock] = []
        current_tokens = 0

        for group in groups:
            would_overflow = current_tokens + group.token_count > self.max_tokens
            if current_blocks and would_overflow:
                chunks.append(_Chunk(blocks=current_blocks, token_count=current_tokens))
                current_blocks = []
                current_tokens = 0

            current_blocks.extend(group.blocks)
            current_tokens += group.token_count

        if current_blocks:
            chunks.append(_Chunk(blocks=current_blocks, token_count=current_tokens))
        return chunks

    def _to_section(self, document: Document, part_number: int, chunk: _Chunk) -> Section:
        title = f"{document.title} — Part {part_number}"
        page_indices = [block.page_idx for block in chunk.blocks if block.page_idx is not None]
        text = "\n\n".join(block.text.strip() for block in chunk.blocks if block.text.strip())
        return Section(
            id=f"{document.doc_id}.part-{part_number}",
            doc_id=document.doc_id,
            title=title,
            level=1,
            heading_path=[title],
            page_start=min(page_indices) if page_indices else None,
            page_end=max(page_indices) if page_indices else None,
            text=text,
            block_ids=[block.id for block in chunk.blocks],
            metadata={"segmenter": self.name, "token_count": chunk.token_count},
        )


def _segmentable_blocks(blocks: list[CanonicalBlock]) -> list[CanonicalBlock]:
    return [
        block
        for block in blocks
        if (block.type in _TEXT_BLOCK_TYPES and block.text.strip())
        or block.type in _NON_TEXT_PROVENANCE_TYPES
    ]


def _page_groups(blocks: list[CanonicalBlock]) -> list[_BlockGroup]:
    groups: list[_BlockGroup] = []
    current: list[CanonicalBlock] = []
    current_page: int | None = None

    for block in blocks:
        starts_new_group = (
            current
            and block.page_idx is not None
            and current_page is not None
            and block.page_idx != current_page
        )
        if starts_new_group:
            groups.append(_group(current))
            current = []

        current.append(block)
        if block.page_idx is not None:
            current_page = block.page_idx

    if current:
        groups.append(_group(current))
    return groups


def _group(blocks: list[CanonicalBlock]) -> _BlockGroup:
    return _BlockGroup(
        blocks=blocks,
        token_count=sum(_token_count(block.text) for block in blocks),
    )


def _token_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text))
