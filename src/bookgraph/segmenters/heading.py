from __future__ import annotations

from dataclasses import dataclass

from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.ports import DocumentSegmenter
from bookgraph.utils import unique_slug


@dataclass
class HeadingSegmenter(DocumentSegmenter):
    """Segment documents at title blocks while preserving heading ancestry."""

    target_level: int = 2
    name: str = "heading"

    def segment(self, document: Document) -> list[Section]:
        sections: list[_DraftSection] = []
        current: _DraftSection | None = None
        heading_stack: list[tuple[int, str]] = []

        for block in document.blocks:
            if block.type == "title":
                level = block.level or 1
                heading_stack = [(lvl, text) for lvl, text in heading_stack if lvl < level]
                heading_stack.append((level, block.text))
                if current is None or level <= self.target_level:
                    if current is not None:
                        sections.append(current)
                    current = _DraftSection(
                        title=block.text,
                        level=level,
                        heading_path=[text for _, text in heading_stack],
                        blocks=[block],
                        start_page=block.page_idx,
                    )
                else:
                    current.blocks.append(block)
                continue

            if current is None:
                current = _DraftSection(
                    title=f"{document.title} — Front Matter",
                    level=1,
                    heading_path=[f"{document.title} — Front Matter"],
                    blocks=[],
                    start_page=block.page_idx,
                )
            current.blocks.append(block)

        if current is not None:
            sections.append(current)

        materialized: list[Section] = []
        used_slugs: set[str] = set()
        for index, draft in enumerate(sections):
            next_start_page = sections[index + 1].start_page if index + 1 < len(sections) else None
            section_slug = unique_slug(draft.title, used_slugs)
            materialized.append(
                draft.to_section(
                    document.doc_id,
                    next_start_page=next_start_page,
                    section_slug=section_slug,
                )
            )
        for index, section in enumerate(materialized):
            section.prev_id = materialized[index - 1].id if index > 0 else None
            section.next_id = materialized[index + 1].id if index + 1 < len(materialized) else None
        return materialized


@dataclass
class _DraftSection:
    title: str
    level: int
    heading_path: list[str]
    blocks: list[CanonicalBlock]
    start_page: int | None

    def to_section(
        self,
        doc_id: str,
        next_start_page: int | None = None,
        section_slug: str | None = None,
    ) -> Section:
        page_indices = [block.page_idx for block in self.blocks if block.page_idx is not None]
        page_end = max(page_indices) if page_indices else None
        if next_start_page is not None:
            page_end = next_start_page - 1
        text_parts = [
            block.text.strip()
            for block in self.blocks
            if block.type != "title" and block.text.strip()
        ]
        return Section(
            id=f"{doc_id}.{section_slug or unique_slug(self.title, set())}",
            doc_id=doc_id,
            title=self.title,
            level=self.level,
            heading_path=self.heading_path,
            page_start=min(page_indices) if page_indices else self.start_page,
            page_end=page_end,
            text="\n\n".join(text_parts),
            block_ids=[block.id for block in self.blocks],
        )
