from __future__ import annotations

from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.segmenters.heading import HeadingSegmenter


def test_heading_segmenter_creates_ordered_sections_from_title_blocks() -> None:
    document = Document(
        doc_id="ddia",
        title="Designing Data-Intensive Applications",
        blocks=[
            CanonicalBlock(id="b1", type="title", text="Chapter 3. Storage", level=1, page_idx=10),
            CanonicalBlock(id="b2", type="text", text="Opening paragraph.", page_idx=10),
            CanonicalBlock(
                id="b3",
                type="title",
                text="SSTables and LSM-Trees",
                level=2,
                page_idx=12,
            ),
            CanonicalBlock(id="b4", type="text", text="LSM content.", page_idx=12),
            CanonicalBlock(id="b5", type="title", text="B-Trees", level=2, page_idx=15),
            CanonicalBlock(id="b6", type="text", text="B-tree content.", page_idx=15),
        ],
    )

    sections = HeadingSegmenter(target_level=2).segment(document)

    assert sections == [
        Section(
            id="ddia.chapter-3-storage",
            doc_id="ddia",
            title="Chapter 3. Storage",
            level=1,
            heading_path=["Chapter 3. Storage"],
            page_start=10,
            page_end=11,
            text="Opening paragraph.",
            prev_id=None,
            next_id="ddia.sstables-and-lsm-trees",
            block_ids=["b1", "b2"],
        ),
        Section(
            id="ddia.sstables-and-lsm-trees",
            doc_id="ddia",
            title="SSTables and LSM-Trees",
            level=2,
            heading_path=["Chapter 3. Storage", "SSTables and LSM-Trees"],
            page_start=12,
            page_end=14,
            text="LSM content.",
            prev_id="ddia.chapter-3-storage",
            next_id="ddia.b-trees",
            block_ids=["b3", "b4"],
        ),
        Section(
            id="ddia.b-trees",
            doc_id="ddia",
            title="B-Trees",
            level=2,
            heading_path=["Chapter 3. Storage", "B-Trees"],
            page_start=15,
            page_end=15,
            text="B-tree content.",
            prev_id="ddia.sstables-and-lsm-trees",
            next_id=None,
            block_ids=["b5", "b6"],
        ),
    ]


def test_heading_segmenter_wraps_intro_text_before_first_heading() -> None:
    document = Document(
        doc_id="paper",
        title="A Paper",
        blocks=[
            CanonicalBlock(id="b1", type="text", text="Abstract text.", page_idx=0),
            CanonicalBlock(id="b2", type="title", text="Introduction", level=1, page_idx=1),
            CanonicalBlock(id="b3", type="text", text="Intro text.", page_idx=1),
        ],
    )

    sections = HeadingSegmenter().segment(document)

    assert [section.title for section in sections] == ["A Paper — Front Matter", "Introduction"]
    assert sections[0].text == "Abstract text."
    assert sections[0].next_id == "paper.introduction"


def test_heading_segmenter_suffixes_duplicate_section_slugs() -> None:
    document = Document(
        doc_id="paper",
        title="A Paper",
        blocks=[
            CanonicalBlock(id="b1", type="title", text="Summary", level=1, page_idx=1),
            CanonicalBlock(id="b2", type="text", text="First summary.", page_idx=1),
            CanonicalBlock(id="b3", type="title", text="Summary", level=1, page_idx=2),
            CanonicalBlock(id="b4", type="text", text="Second summary.", page_idx=2),
            CanonicalBlock(id="b5", type="title", text="Summary", level=1, page_idx=3),
            CanonicalBlock(id="b6", type="text", text="Third summary.", page_idx=3),
        ],
    )

    sections = HeadingSegmenter(target_level=1).segment(document)

    assert [section.id for section in sections] == [
        "paper.summary",
        "paper.summary-2",
        "paper.summary-3",
    ]
    assert sections[0].next_id == "paper.summary-2"
    assert sections[1].prev_id == "paper.summary"
    assert sections[1].next_id == "paper.summary-3"
    assert sections[2].prev_id == "paper.summary-2"


def test_heading_segmenter_avoids_suffix_collisions_with_existing_titles() -> None:
    document = Document(
        doc_id="paper",
        title="A Paper",
        blocks=[
            CanonicalBlock(id="b1", type="title", text="Summary", level=1),
            CanonicalBlock(id="b2", type="text", text="First."),
            CanonicalBlock(id="b3", type="title", text="Summary 2", level=1),
            CanonicalBlock(id="b4", type="text", text="Already suffixed title."),
            CanonicalBlock(id="b5", type="title", text="Summary", level=1),
            CanonicalBlock(id="b6", type="text", text="Second summary."),
        ],
    )

    sections = HeadingSegmenter(target_level=1).segment(document)

    assert [section.id for section in sections] == [
        "paper.summary",
        "paper.summary-2",
        "paper.summary-3",
    ]
