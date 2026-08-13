from __future__ import annotations

from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.segmenters.token_page import TokenPageSegmenter


def test_token_page_segmenter_splits_long_documents_by_token_budget() -> None:
    document = Document(
        doc_id="paper",
        title="A Paper",
        blocks=[
            CanonicalBlock(id="b1", type="text", text="alpha beta gamma", page_idx=1),
            CanonicalBlock(id="b2", type="text", text="delta epsilon", page_idx=1),
            CanonicalBlock(id="b3", type="text", text="zeta eta theta", page_idx=2),
        ],
    )

    sections = TokenPageSegmenter(max_tokens=5).segment(document)

    assert sections == [
        Section(
            id="paper.part-1",
            doc_id="paper",
            title="A Paper — Part 1",
            level=1,
            heading_path=["A Paper — Part 1"],
            page_start=1,
            page_end=1,
            text="alpha beta gamma\n\ndelta epsilon",
            prev_id=None,
            next_id="paper.part-2",
            block_ids=["b1", "b2"],
            metadata={"segmenter": "token-page", "token_count": 5},
        ),
        Section(
            id="paper.part-2",
            doc_id="paper",
            title="A Paper — Part 2",
            level=1,
            heading_path=["A Paper — Part 2"],
            page_start=2,
            page_end=2,
            text="zeta eta theta",
            prev_id="paper.part-1",
            next_id=None,
            block_ids=["b3"],
            metadata={"segmenter": "token-page", "token_count": 3},
        ),
    ]


def test_token_page_segmenter_prefers_page_boundaries_when_possible() -> None:
    document = Document(
        doc_id="paper",
        title="A Paper",
        blocks=[
            CanonicalBlock(id="p1a", type="text", text="one two", page_idx=1),
            CanonicalBlock(id="p1b", type="text", text="three four", page_idx=1),
            CanonicalBlock(id="p2", type="text", text="five six", page_idx=2),
        ],
    )

    sections = TokenPageSegmenter(max_tokens=5).segment(document)

    assert [section.block_ids for section in sections] == [["p1a", "p1b"], ["p2"]]
    assert [section.page_start for section in sections] == [1, 2]
    assert [section.page_end for section in sections] == [1, 2]


def test_token_page_segmenter_keeps_a_page_together_even_if_last_block_overflows() -> None:
    document = Document(
        doc_id="paper",
        title="A Paper",
        blocks=[
            CanonicalBlock(id="p1a", type="text", text="one two", page_idx=1),
            CanonicalBlock(id="p1b", type="text", text="three four", page_idx=1),
            CanonicalBlock(id="p1c", type="text", text="five six", page_idx=1),
            CanonicalBlock(id="p2", type="text", text="seven", page_idx=2),
        ],
    )

    sections = TokenPageSegmenter(max_tokens=5).segment(document)

    assert [section.block_ids for section in sections] == [["p1a", "p1b", "p1c"], ["p2"]]
    assert sections[0].metadata["token_count"] == 6


def test_token_page_segmenter_preserves_non_textual_block_provenance() -> None:
    document = Document(
        doc_id="paper",
        title="A Paper",
        blocks=[
            CanonicalBlock(id="intro", type="text", text="body", page_idx=1),
            CanonicalBlock(id="fig", type="image", text="", page_idx=1),
            CanonicalBlock(id="chart", type="chart", text="", page_idx=2),
            CanonicalBlock(id="next", type="text", text="next body", page_idx=2),
        ],
    )

    sections = TokenPageSegmenter(max_tokens=10).segment(document)

    assert [section.block_ids for section in sections] == [["intro", "fig", "chart", "next"]]
    assert sections[0].text == "body\n\nnext body"


def test_token_page_segmenter_keeps_oversized_single_block_whole() -> None:
    document = Document(
        doc_id="paper",
        title="A Paper",
        blocks=[
            CanonicalBlock(
                id="b1",
                type="text",
                text="one two three four five six",
                page_idx=1,
            )
        ],
    )

    sections = TokenPageSegmenter(max_tokens=3).segment(document)

    assert len(sections) == 1
    assert sections[0].text == "one two three four five six"
    assert sections[0].metadata["token_count"] == 6


def test_token_page_segmenter_ignores_empty_unknown_blocks() -> None:
    document = Document(
        doc_id="paper",
        title="A Paper",
        blocks=[
            CanonicalBlock(id="empty", type="unknown", text="", page_idx=1),
            CanonicalBlock(id="txt", type="text", text="body", page_idx=1),
        ],
    )

    sections = TokenPageSegmenter(max_tokens=10).segment(document)

    assert [section.block_ids for section in sections] == [["txt"]]
    assert sections[0].text == "body"
