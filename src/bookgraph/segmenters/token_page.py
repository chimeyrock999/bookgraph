from __future__ import annotations

import re
from dataclasses import dataclass

from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.ports import DocumentSegmenter

_TOKEN_RE = re.compile(r"\S+")
_CONTENT_BLOCK_TYPES = {"title", "text", "list", "table", "equation", "unknown"}


@dataclass
class TokenPageSegmenter(DocumentSegmenter):
    """Fallback segmenter that chunks by token budget while respecting pages.

    It is intended for documents without useful headings or PDF bookmarks. Blocks
    are kept whole for provenance; an oversized block becomes its own section.
    When possible, the segmenter avoids crossing page boundaries if adding the
    next page would exceed the configured token budget.
    """

    max_tokens: int = 800
    name: str = "token-page"

    def segment(self, document: Document) -> list[Section]:
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")

        chunks = self._chunks(_content_blocks(document.blocks))
        sections = [
            self._to_section(document, index, blocks)
            for index, blocks in enumerate(chunks, 1)
        ]
        for index, section in enumerate(sections):
            section.prev_id = sections[index - 1].id if index > 0 else None
            section.next_id = sections[index + 1].id if index + 1 < len(sections) else None
        return sections

    def _chunks(self, blocks: list[CanonicalBlock]) -> list[list[CanonicalBlock]]:
        chunks: list[list[CanonicalBlock]] = []
        current: list[CanonicalBlock] = []
        current_tokens = 0
        current_page: int | None = None

        for block in blocks:
            block_tokens = _token_count(block.text)
            page_changes = (
                current_page is not None
                and block.page_idx is not None
                and block.page_idx != current_page
            )
            would_overflow = current_tokens + block_tokens > self.max_tokens
            if current and would_overflow and (page_changes or current_tokens > 0):
                chunks.append(current)
                current = []
                current_tokens = 0

            current.append(block)
            current_tokens += block_tokens
            if block.page_idx is not None:
                current_page = block.page_idx

        if current:
            chunks.append(current)
        return chunks

    def _to_section(
        self,
        document: Document,
        part_number: int,
        blocks: list[CanonicalBlock],
    ) -> Section:
        title = f"{document.title} — Part {part_number}"
        page_indices = [block.page_idx for block in blocks if block.page_idx is not None]
        text = "\n\n".join(block.text.strip() for block in blocks if block.text.strip())
        return Section(
            id=f"{document.doc_id}.part-{part_number}",
            doc_id=document.doc_id,
            title=title,
            level=1,
            heading_path=[title],
            page_start=min(page_indices) if page_indices else None,
            page_end=max(page_indices) if page_indices else None,
            text=text,
            block_ids=[block.id for block in blocks],
            metadata={"segmenter": self.name, "token_count": _token_count(text)},
        )


def _content_blocks(blocks: list[CanonicalBlock]) -> list[CanonicalBlock]:
    return [
        block
        for block in blocks
        if block.type in _CONTENT_BLOCK_TYPES and block.text.strip()
    ]


def _token_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text))
