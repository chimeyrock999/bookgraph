from __future__ import annotations

from dataclasses import dataclass

from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.ports import DocumentSegmenter
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.utils import slugify


@dataclass(frozen=True)
class PdfBookmark:
    title: str
    page_index: int | None
    level: int


@dataclass
class BookmarkSegmenter(DocumentSegmenter):
    """Segment a document by PDF outline/bookmark page boundaries.

    The segmenter is deterministic and lightweight: it consumes bookmarks already
    captured in `sources/inbox/<book_id>/book.json` by `add-book`, then maps their
    page starts onto canonical parser blocks. If no usable bookmarks are present,
    it falls back to the heading segmenter.
    """

    bookmarks: list[PdfBookmark]
    split_level: int = 1
    fallback: HeadingSegmenter | None = None
    name: str = "bookmark"

    def segment(self, document: Document) -> list[Section]:
        usable = [
            bookmark
            for bookmark in self.bookmarks
            if bookmark.page_index is not None and bookmark.level <= self.split_level
        ]
        usable.sort(key=lambda bookmark: (bookmark.page_index or 0, bookmark.level, bookmark.title))
        if not usable:
            return (self.fallback or HeadingSegmenter()).segment(document)

        sections: list[Section] = []
        for index, bookmark in enumerate(usable):
            start_page = bookmark.page_index
            next_page = usable[index + 1].page_index if index + 1 < len(usable) else None
            blocks = _blocks_in_range(document.blocks, start_page, next_page)
            sections.append(_to_section(document.doc_id, bookmark, blocks, next_page))

        for index, section in enumerate(sections):
            section.prev_id = sections[index - 1].id if index > 0 else None
            section.next_id = sections[index + 1].id if index + 1 < len(sections) else None
        return sections


def _blocks_in_range(
    blocks: list[CanonicalBlock], start_page: int | None, next_page: int | None
) -> list[CanonicalBlock]:
    if start_page is None:
        return []
    selected: list[CanonicalBlock] = []
    for block in blocks:
        if block.page_idx is None:
            continue
        if block.page_idx < start_page:
            continue
        if next_page is not None and block.page_idx >= next_page:
            continue
        selected.append(block)
    return selected


def _to_section(
    doc_id: str, bookmark: PdfBookmark, blocks: list[CanonicalBlock], next_page: int | None
) -> Section:
    page_indices = [block.page_idx for block in blocks if block.page_idx is not None]
    text_parts = [
        block.text.strip()
        for block in blocks
        if block.type != "title" and block.text.strip()
    ]
    page_start = bookmark.page_index
    page_end = max(page_indices) if page_indices else None
    if next_page is not None:
        page_end = next_page - 1
    return Section(
        id=f"{doc_id}.{slugify(bookmark.title)}",
        doc_id=doc_id,
        title=bookmark.title,
        level=bookmark.level,
        heading_path=[bookmark.title],
        page_start=page_start,
        page_end=page_end,
        text="\n\n".join(text_parts),
        block_ids=[block.id for block in blocks],
        metadata={
            "segmenter": "bookmark",
            "bookmark_level": bookmark.level,
            "bookmark_page_index": bookmark.page_index,
        },
    )
