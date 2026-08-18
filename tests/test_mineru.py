from __future__ import annotations

import json
from pathlib import Path

from bookgraph.parsers.mineru import MinerUMiddleJsonParser


def _write_middle_json(tmp_path: Path) -> Path:
    """A minimal MinerU middle-JSON payload with an image and a table block.

    Mirrors MinerU's nested ``para_blocks`` shape: the asset file lives on the body
    span (``image_path``) while the human-readable caption is a separate text span.
    """

    payload = {
        "pdf_info": [
            {
                "page_idx": 4,
                "para_blocks": [
                    {"type": "title", "lines": [{"spans": [{"content": "Chapter"}]}]},
                    {
                        "type": "image",
                        "bbox": [0, 0, 10, 10],
                        "blocks": [
                            {
                                "type": "image_body",
                                "lines": [
                                    {"spans": [{"type": "image", "image_path": "fig1.jpg"}]}
                                ],
                            },
                            {
                                "type": "image_caption",
                                "lines": [
                                    {"spans": [{"content": "Figure 3.1 architecture"}]}
                                ],
                            },
                        ],
                    },
                    {
                        "type": "table",
                        "blocks": [
                            {
                                "type": "table_body",
                                "lines": [
                                    {
                                        "spans": [
                                            {
                                                "type": "table",
                                                "html": "<table><tr><td>x</td></tr></table>",
                                                "image_path": "tbl1.jpg",
                                            }
                                        ]
                                    }
                                ],
                            },
                            {
                                "type": "table_caption",
                                "lines": [{"spans": [{"content": "Table 4.2 results"}]}],
                            },
                        ],
                    },
                ],
            }
        ]
    }
    source = tmp_path / "book_middle.json"
    source.write_text(json.dumps(payload))
    return source


def test_mineru_parser_captures_asset_path_and_caption(tmp_path: Path) -> None:
    source = _write_middle_json(tmp_path)

    document = MinerUMiddleJsonParser().parse(source, tmp_path)

    image = next(block for block in document.blocks if block.type == "image")
    assert image.asset_path == "fig1.jpg"
    assert image.text == "Figure 3.1 architecture"

    table = next(block for block in document.blocks if block.type == "table")
    assert table.asset_path == "tbl1.jpg"
    # The table body HTML lives on a ``html`` span, not ``content``: only the caption
    # surfaces as text, so the structured asset is the only route to the table content.
    assert table.text == "Table 4.2 results"


def test_mineru_parser_leaves_text_blocks_without_asset_path(tmp_path: Path) -> None:
    source = _write_middle_json(tmp_path)

    document = MinerUMiddleJsonParser().parse(source, tmp_path)

    title = next(block for block in document.blocks if block.type == "title")
    assert title.asset_path is None


def test_mineru_parser_ignores_image_path_on_non_body_spans(tmp_path: Path) -> None:
    # Only the body span (type image/table) owns the asset; a caption span that happens to
    # carry an image_path must not be mistaken for the block's real asset.
    payload = {
        "pdf_info": [
            {
                "page_idx": 0,
                "para_blocks": [
                    {
                        "type": "image",
                        "blocks": [
                            {
                                "type": "image_caption",
                                "lines": [
                                    {"spans": [{"type": "text", "image_path": "WRONG.jpg"}]}
                                ],
                            },
                            {
                                "type": "image_body",
                                "lines": [
                                    {"spans": [{"type": "image", "image_path": "right.jpg"}]}
                                ],
                            },
                        ],
                    }
                ],
            }
        ]
    }
    source = tmp_path / "b_middle.json"
    source.write_text(json.dumps(payload))

    document = MinerUMiddleJsonParser().parse(source, tmp_path)

    image = next(block for block in document.blocks if block.type == "image")
    assert image.asset_path == "right.jpg"
