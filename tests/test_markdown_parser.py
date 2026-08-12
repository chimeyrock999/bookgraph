from __future__ import annotations

from pathlib import Path

from bookgraph.parsers.markdown import MarkdownParser

SAMPLE = """# Deep Work

Opening paragraph.

## Rule 1: Work Deeply

- focus blocks
- shutdown ritual

| tool | cost |
| --- | --- |
| email | high |

$$e = mc^2$$

```python
print("hi")
```

![flow diagram](img/flow.png)

> Attention residue is real.
"""


def test_markdown_parser_maps_each_construct_to_a_canonical_block(tmp_path: Path) -> None:
    source = tmp_path / "Deep Work.md"
    source.write_text(SAMPLE)

    document = MarkdownParser().parse(source, tmp_path / "parsed")

    assert document.doc_id == "deep-work"
    assert document.title == "Deep Work"
    assert [(block.type, block.level) for block in document.blocks] == [
        ("title", 1),
        ("text", None),
        ("title", 2),
        ("list", None),
        ("table", None),
        ("equation", None),
        ("text", None),
        ("image", None),
        ("text", None),
    ]


def test_markdown_parser_preserves_block_content_and_provenance(tmp_path: Path) -> None:
    source = tmp_path / "Deep Work.md"
    source.write_text(SAMPLE)

    blocks = MarkdownParser().parse(source, tmp_path / "parsed").blocks

    assert blocks[0].text == "Deep Work"
    assert blocks[2].text == "Rule 1: Work Deeply"
    assert blocks[3].text == "- focus blocks\n- shutdown ritual"
    assert blocks[4].text == "tool | cost\nemail | high"
    assert blocks[5].text == "e = mc^2"
    assert blocks[6].text == 'print("hi")'
    assert blocks[6].metadata["language"] == "python"
    assert blocks[7].metadata["src"] == "img/flow.png"
    assert blocks[7].text == "flow diagram"
    assert blocks[8].metadata["quote"] is True
    assert [block.id for block in blocks] == [f"b{index}" for index in range(len(blocks))]
    assert [block.order for block in blocks] == list(range(len(blocks)))
    assert {block.source_path for block in blocks} == {str(source)}
    assert all(block.page_idx is None for block in blocks)


def test_markdown_parser_records_source_line_ranges_as_provenance(tmp_path: Path) -> None:
    source = tmp_path / "notes.md"
    source.write_text(SAMPLE)

    blocks = MarkdownParser().parse(source, tmp_path / "parsed").blocks

    assert blocks[0].metadata["line_start"] == 1
    assert blocks[0].metadata["line_end"] == 1
    assert blocks[1].metadata["line_start"] == 3
    assert blocks[3].metadata["line_start"] == 7
    # markdown-it's container map runs to the blank line after the list.
    assert blocks[3].metadata["line_end"] == 9
    assert all("line_start" in block.metadata for block in blocks)


def test_markdown_parser_falls_back_to_filename_when_no_h1(tmp_path: Path) -> None:
    source = tmp_path / "Field Notes.md"
    source.write_text("Loose intro.\n\n## Later Heading\n\nBody.\n")

    document = MarkdownParser().parse(source, tmp_path / "parsed")

    assert document.title == "Field Notes"
    assert document.doc_id == "field-notes"
    assert document.metadata["parser"] == "markdown"
    assert document.metadata["source_path"] == str(source)


def test_markdown_parser_keeps_nested_list_as_one_block(tmp_path: Path) -> None:
    source = tmp_path / "nested.md"
    source.write_text("# T\n\n- outer\n  - inner\n- second\n\nAfter list.\n")

    blocks = MarkdownParser().parse(source, tmp_path / "parsed").blocks

    assert [block.type for block in blocks] == ["title", "list", "text"]
    assert blocks[1].text == "- outer\n- inner\n- second"
    assert blocks[2].text == "After list."


def test_markdown_parser_writes_nothing_to_output_dir(tmp_path: Path) -> None:
    source = tmp_path / "notes.md"
    source.write_text("# T\n\nBody.\n")
    output_dir = tmp_path / "parsed"

    MarkdownParser().parse(source, output_dir)

    assert not output_dir.exists()
