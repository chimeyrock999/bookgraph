"""Contract tests against MinerU 4 output recorded from ``mineru-kit parse --format zip``.

The fixtures under ``fixtures/mineru4/<tier>/`` are real MinerU 4.0.10 bundles for a
three-page born-digital PDF (running header, page numbers, two chapters with a
sub-heading, one figure with a caption), so CI checks the adapter against the
shipped schema without installing MinerU or its models.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from bookgraph.assets import resolve_asset_path
from bookgraph.parsers.errors import UnsupportedSourceError
from bookgraph.parsers.mineru import MinerUMiddleJsonParser
from bookgraph.segmenters.heading import HeadingSegmenter

FIXTURES = Path(__file__).parent / "fixtures" / "mineru4"
TIERS = ["flash", "basic"]


def _stage(tmp_path: Path, tier: str) -> Path:
    """Stage a recorded bundle the way ``MinerURunner`` lays it out."""

    parsed_dir = tmp_path / "parsed" / "book"
    parsed_dir.mkdir(parents=True)
    shutil.copy(FIXTURES / tier / "middle_json.json", parsed_dir / "book_middle.json")
    (parsed_dir / "images").mkdir()
    (parsed_dir / "images" / "page_1_image_4.jpg").write_bytes(b"jpg")
    return parsed_dir


@pytest.mark.parametrize("tier", TIERS)
def test_recorded_bundle_parses_into_reading_blocks(tmp_path: Path, tier: str) -> None:
    parsed_dir = _stage(tmp_path, tier)

    document = MinerUMiddleJsonParser().parse(parsed_dir / "book_middle.json", parsed_dir)

    assert document.doc_id == "book"
    assert document.metadata["mineru_schema"] == "docvortex.middle/2.0"
    assert document.metadata["mineru_version"] == "4.0.10"
    assert document.metadata["mineru_tier"] == tier
    texts = [block.text for block in document.blocks]
    # Running headers and page numbers are page furniture, not reading content.
    assert "1" not in texts and "2" not in texts
    assert all(block.metadata["mineru_type"] != "header" for block in document.blocks)
    titles = [block.text for block in document.blocks if block.type == "title"]
    assert titles[-4:] == [
        "Chapter 1 Replication",
        "1.1 Leaders and Followers",
        "Chapter 2 Storage",
        "2.1 Log-Structured Engines",
    ]
    assert [block.order for block in document.blocks] == list(range(len(document.blocks)))


@pytest.mark.parametrize("tier", TIERS)
def test_block_ids_keep_mineru_page_and_block_index(tmp_path: Path, tier: str) -> None:
    parsed_dir = _stage(tmp_path, tier)
    payload = json.loads((parsed_dir / "book_middle.json").read_text())

    document = MinerUMiddleJsonParser().parse(parsed_dir / "book_middle.json", parsed_dir)

    expected = {
        f"p{page['page_idx']}.b{block['index']}"
        for page in payload["pages"]
        for block in page["blocks"]
        if block["type"] not in {"header", "footer", "page_number", "aside_text"}
    }
    assert {block.id for block in document.blocks} == expected
    # The ids line up with MinerU's ``page:{page}/block:{index}`` locators.
    chapter = next(block for block in document.blocks if block.text == "Chapter 2 Storage")
    assert (chapter.id, chapter.page_idx) == ("p2.b1", 2)


@pytest.mark.parametrize("tier", TIERS)
def test_figure_keeps_its_asset_and_caption(tmp_path: Path, tier: str) -> None:
    parsed_dir = _stage(tmp_path, tier)

    document = MinerUMiddleJsonParser().parse(parsed_dir / "book_middle.json", parsed_dir)

    [figure] = [block for block in document.blocks if block.type == "image"]
    assert figure.asset_path == "images/page_1_image_4.jpg"
    assert figure.text == "Figure 1-1. A leader and two followers."
    assert figure.page_idx == 1
    # The staged reference opens a real file through the shared asset resolver.
    assert resolve_asset_path(parsed_dir, figure) == str(
        parsed_dir / "images" / "page_1_image_4.jpg"
    )


@pytest.mark.parametrize("tier", TIERS)
def test_recorded_bundle_segments_into_chapter_sections(tmp_path: Path, tier: str) -> None:
    parsed_dir = _stage(tmp_path, tier)
    document = MinerUMiddleJsonParser().parse(parsed_dir / "book_middle.json", parsed_dir)

    sections = HeadingSegmenter(target_level=2).segment(document)

    by_title = {section.title: section for section in sections}
    leaders = by_title["1.1 Leaders and Followers"]
    assert "A leader accepts writes" in leaders.text
    assert leaders.page_start == 1
    assert "Indexes speed up reads" in by_title["2.1 Log-Structured Engines"].text
    assert by_title["Chapter 2 Storage"].page_start == 2


def test_heading_levels_come_from_mineru(tmp_path: Path) -> None:
    payload = {
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "metadata": {"file_suffix": "pdf", "producer": {"name": "mineru", "version": "4"}},
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {"type": "doc_title", "index": 0, "level": 1, "content": [_span("Book")]},
                    {
                        "type": "paragraph_title",
                        "index": 1,
                        "level": 3,
                        "content": [_span("Deep")],
                    },
                ],
            }
        ],
        "is_full_document": True,
    }
    source = tmp_path / "b_middle.json"
    source.write_text(json.dumps(payload))

    document = MinerUMiddleJsonParser().parse(source, tmp_path)

    assert [(block.type, block.level) for block in document.blocks] == [
        ("title", 1),
        ("title", 3),
    ]
    assert document.title == "Book"


def test_nested_list_table_and_equation_content_is_rendered(tmp_path: Path) -> None:
    payload = {
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "metadata": {
            "file_suffix": "pdf",
            "producer": {"name": "mineru", "version": "4"},
            "document": {"title": "Declared Title"},
        },
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "type": "list",
                        "index": 0,
                        "content": [
                            {"type": "text", "content": [_span("first")]},
                            {
                                "type": "text",
                                "content": [
                                    {
                                        "type": "hyperlink",
                                        "url": "https://x",
                                        "content": [_span("second")],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "type": "table",
                        "index": 1,
                        "content": [
                            {
                                "type": "table_body",
                                "image_path": "images/t.jpg",
                                "content": "<table><tr><td>x</td></tr></table>",
                            },
                            {"type": "table_caption", "content": [_span("Table 1")]},
                            {"type": "table_footnote", "content": [_span("Source: us")]},
                        ],
                    },
                    {"type": "equation", "index": 2, "content": "E = mc^2"},
                    {"type": "page_footnote", "index": 3, "content": [_span("A note.")]},
                    {"type": "footer", "index": 4, "content": [_span("Footer")]},
                ],
            }
        ],
        "is_full_document": True,
    }
    source = tmp_path / "b_middle.json"
    source.write_text(json.dumps(payload))

    document = MinerUMiddleJsonParser().parse(source, tmp_path)

    assert [(block.type, block.text) for block in document.blocks] == [
        # List items are child blocks; the hyperlink inside one stays a Markdown link.
        ("list", "first [second](https://x)"),
        ("table", "Table 1 Source: us"),
        ("equation", "E = mc^2"),
        ("text", "A note."),
    ]
    assert document.blocks[1].asset_path == "images/t.jpg"
    assert document.title == "Declared Title"


def test_inline_spans_join_verbatim(tmp_path: Path) -> None:
    payload = {
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "metadata": {"file_suffix": "pdf"},
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "type": "text",
                        "index": 0,
                        "content": [
                            _span("See ("),
                            {"type": "code_inline", "content": "x[0]"},
                            _span(", "),
                            {"type": "equation_inline", "content": "a+b"},
                            _span(") ["),
                            {"type": "hyperlink", "url": "#ref 3", "content": [_span("3")]},
                            _span("]."),
                        ],
                    }
                ],
            }
        ],
    }
    source = tmp_path / "b_middle.json"
    source.write_text(json.dumps(payload))

    (block,) = MinerUMiddleJsonParser().parse(source, tmp_path).blocks

    assert block.text == "See (`x[0]`, $a+b$) [[3](#ref%203)]."


def test_unknown_schema_major_version_fails_loudly(tmp_path: Path) -> None:
    source = tmp_path / "b_middle.json"
    source.write_text(
        json.dumps({"schema": "docvortex.middle", "schema_version": "3.0", "pages": []})
    )

    with pytest.raises(UnsupportedSourceError, match="schema_version '3.0'"):
        MinerUMiddleJsonParser().parse(source, tmp_path)


def test_mineru_3_middle_json_still_parses(tmp_path: Path) -> None:
    source = tmp_path / "old_middle.json"
    source.write_text(
        json.dumps(
            {
                "pdf_info": [
                    {
                        "page_idx": 0,
                        "para_blocks": [
                            {"type": "title", "lines": [{"spans": [{"content": "Old"}]}]}
                        ],
                    }
                ]
            }
        )
    )

    document = MinerUMiddleJsonParser().parse(source, tmp_path)

    assert [block.text for block in document.blocks] == ["Old"]


def _span(text: str) -> dict[str, str]:
    return {"type": "text", "content": text}
