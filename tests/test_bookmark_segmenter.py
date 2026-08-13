from __future__ import annotations

from bookgraph.models import CanonicalBlock, Document
from bookgraph.segmenters.bookmark import BookmarkSegmenter, PdfBookmark


def _document() -> Document:
    return Document(
        doc_id="iceberg",
        title="Iceberg",
        blocks=[
            CanonicalBlock(id="b0", type="text", text="Preface text", page_idx=0, order=0),
            CanonicalBlock(id="b1", type="text", text="Chapter one opening", page_idx=1, order=1),
            CanonicalBlock(id="b2", type="text", text="Chapter one continued", page_idx=2, order=2),
            CanonicalBlock(id="b3", type="text", text="Chapter two opening", page_idx=3, order=3),
            CanonicalBlock(id="b4", type="text", text="Appendix", page_idx=5, order=4),
        ],
    )


def test_bookmark_segmenter_uses_top_level_pdf_bookmarks_as_reading_units() -> None:
    segmenter = BookmarkSegmenter(
        bookmarks=[
            PdfBookmark(title="Chapter 1", page_index=1, level=1),
            PdfBookmark(title="Chapter 2", page_index=3, level=1),
        ]
    )

    sections = segmenter.segment(_document())

    assert [section.id for section in sections] == ["iceberg.chapter-1", "iceberg.chapter-2"]
    assert [section.title for section in sections] == ["Chapter 1", "Chapter 2"]
    assert [section.page_start for section in sections] == [1, 3]
    assert [section.page_end for section in sections] == [2, 5]
    assert [section.text for section in sections] == [
        "Chapter one opening\n\nChapter one continued",
        "Chapter two opening\n\nAppendix",
    ]
    assert [section.block_ids for section in sections] == [["b1", "b2"], ["b3", "b4"]]
    assert sections[0].next_id == "iceberg.chapter-2"
    assert sections[1].prev_id == "iceberg.chapter-1"


def test_bookmark_segmenter_collapses_nested_bookmarks_at_split_level() -> None:
    segmenter = BookmarkSegmenter(
        bookmarks=[
            PdfBookmark(title="Part I", page_index=1, level=1),
            PdfBookmark(title="Chapter 1", page_index=2, level=2),
            PdfBookmark(title="Chapter 2", page_index=3, level=2),
        ],
        split_level=1,
    )

    sections = segmenter.segment(_document())

    assert [section.title for section in sections] == ["Part I"]
    assert sections[0].heading_path == ["Part I"]
    assert sections[0].metadata["bookmark_level"] == 1
    assert sections[0].metadata["bookmark_page_index"] == 1


def test_bookmark_segmenter_suffixes_duplicate_bookmark_titles() -> None:
    segmenter = BookmarkSegmenter(
        bookmarks=[
            PdfBookmark(title="Summary", page_index=1, level=1),
            PdfBookmark(title="Summary", page_index=3, level=1),
        ]
    )

    sections = segmenter.segment(_document())

    assert [section.id for section in sections] == ["iceberg.summary", "iceberg.summary-2"]


def test_bookmark_segmenter_avoids_suffix_collisions_with_existing_titles() -> None:
    segmenter = BookmarkSegmenter(
        bookmarks=[
            PdfBookmark(title="Summary", page_index=1, level=1),
            PdfBookmark(title="Summary 2", page_index=2, level=1),
            PdfBookmark(title="Summary", page_index=3, level=1),
        ]
    )

    sections = segmenter.segment(_document())

    assert [section.id for section in sections] == [
        "iceberg.summary",
        "iceberg.summary-2",
        "iceberg.summary-3",
    ]


def test_bookmark_segmenter_clamps_same_page_bookmark_ranges() -> None:
    segmenter = BookmarkSegmenter(
        bookmarks=[
            PdfBookmark(title="First", page_index=1, level=1),
            PdfBookmark(title="Second", page_index=1, level=1),
        ]
    )

    sections = segmenter.segment(_document())

    assert sections[0].page_start == 1
    assert sections[0].page_end == 1


def test_bookmark_segmenter_falls_back_to_heading_when_no_usable_bookmarks() -> None:
    document = Document(
        doc_id="iceberg",
        title="Iceberg",
        blocks=[
            CanonicalBlock(id="t1", type="title", text="Intro", level=1, order=0),
            CanonicalBlock(id="b1", type="text", text="Intro text", order=1),
        ],
    )

    sections = BookmarkSegmenter(bookmarks=[]).segment(document)

    assert [section.id for section in sections] == ["iceberg.intro"]
    assert sections[0].text == "Intro text"
