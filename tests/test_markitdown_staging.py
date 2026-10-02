"""Image staging on the MarkItDown path: data-URI pictures and alt text with backticks."""

from __future__ import annotations

import base64
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest

from bookgraph.assets import resolve_asset_path
from bookgraph.parsers.markitdown import MarkItDownParser
from docx_support import PNG_1X1


class _FakeConversion:
    def __init__(self, text_content: str, flattened_nested_tables: int = 0) -> None:
        self.text_content = text_content
        self.flattened_nested_tables = flattened_nested_tables


class _FakeConverter:
    def __init__(self, text_content: str, flattened_nested_tables: int = 0) -> None:
        self._conversion = _FakeConversion(text_content, flattened_nested_tables)

    def convert(self, source: str) -> _FakeConversion:
        return self._conversion


def _data_uri(data: bytes, media_type: str = "image/png") -> str:
    return f"data:{media_type};base64,{base64.b64encode(data).decode()}"


def _image_blocks(document):  # type: ignore[no-untyped-def]
    return [block for block in document.blocks if block.type == "image"]


def test_data_uri_images_are_decoded_into_images_dir(tmp_path: Path) -> None:
    source = tmp_path / "report.docx"
    source.write_bytes(b"docx")
    output_dir = tmp_path / "parsed" / "report"
    markdown = f"# Report\n\n![Figure 1]({_data_uri(PNG_1X1)})\n\nAfter.\n"

    document = MarkItDownParser(converter=_FakeConverter(markdown)).parse(source, output_dir)

    [image] = _image_blocks(document)
    src = str(image.metadata["src"])
    assert src.startswith("images/image-") and src.endswith(".png")
    assert (output_dir / src).read_bytes() == PNG_1X1
    assert resolve_asset_path(output_dir, image) == str((output_dir / src).resolve())
    staged_md = (output_dir / "report.md").read_text()
    assert "data:" not in staged_md
    assert f"![Figure 1]({src})" in staged_md
    assert "unresolved_image_count" not in document.metadata


def test_identical_data_uri_images_share_one_file(tmp_path: Path) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"pptx")
    output_dir = tmp_path / "parsed" / "deck"
    uri = _data_uri(PNG_1X1)
    jpeg = _data_uri(b"\xff\xd8\xff jpeg", "image/jpeg")
    markdown = f"![a]({uri})\n\n![b]({uri})\n\n![c]({jpeg})\n"

    document = MarkItDownParser(converter=_FakeConverter(markdown)).parse(source, output_dir)

    srcs = [str(block.metadata["src"]) for block in _image_blocks(document)]
    assert srcs[0] == srcs[1]
    assert srcs[2].endswith(".jpg")
    assert sorted(path.name for path in (output_dir / "images").iterdir()) == sorted(
        {Path(src).name for src in srcs}
    )


def test_undecodable_data_uri_is_reported_unresolved(tmp_path: Path) -> None:
    source = tmp_path / "report.docx"
    source.write_bytes(b"docx")
    output_dir = tmp_path / "parsed" / "report"
    # MarkItDown's default truncation: the bytes are gone, so nothing can be staged.
    markdown = "![Figure](data:image/png;base64...)\n"

    with pytest.warns(UserWarning, match="1 image reference"):
        document = MarkItDownParser(converter=_FakeConverter(markdown)).parse(source, output_dir)

    assert document.metadata["unresolved_image_count"] == 1
    assert not (output_dir / "images").exists()


def test_data_uri_inside_code_is_left_alone(tmp_path: Path) -> None:
    source = tmp_path / "guide.html"
    source.write_text("<html/>")
    output_dir = tmp_path / "parsed" / "guide"
    sample = f"![x]({_data_uri(PNG_1X1)})"
    markdown = f"```\n{sample}\n```\n\nInline `{sample}` sample.\n"

    MarkItDownParser(converter=_FakeConverter(markdown)).parse(source, output_dir)

    assert (output_dir / "guide.md").read_text() == markdown
    assert not (output_dir / "images").exists()


def test_epub_image_with_backticks_in_alt_is_staged(tmp_path: Path) -> None:
    figure = b"\x89PNG figure"
    source = tmp_path / "book.epub"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("OEBPS/assets/ddia_0307.png", figure)
    output_dir = tmp_path / "parsed" / "book"
    markdown = (
        "![Diagram of changes in the `within_recursive` table through stages.]"
        "(assets/ddia_0307.png)\n\n"
        "Keep `![code](assets/ddia_0307.png)` verbatim.\n"
    )

    document = MarkItDownParser(converter=_FakeConverter(markdown)).parse(source, output_dir)

    [image] = _image_blocks(document)
    assert image.metadata["src"] == "images/ddia_0307.png"
    assert (output_dir / "images" / "ddia_0307.png").read_bytes() == figure
    staged_md = (output_dir / "book.md").read_text()
    assert "`![code](assets/ddia_0307.png)`" in staged_md
    assert "unresolved_image_count" not in document.metadata


def test_unbalanced_backtick_in_alt_keeps_the_code_span(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("OEBPS/a.png", b"png")
    output_dir = tmp_path / "parsed" / "book"
    # CommonMark reads "`b](a.png) and `" as a code span here, so this is not an image.
    markdown = "![a `b](a.png) and ` tail\n"

    MarkItDownParser(converter=_FakeConverter(markdown)).parse(source, output_dir)

    assert (output_dir / "book.md").read_text() == markdown


def test_lone_backtick_in_alt_still_stages_the_image(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("OEBPS/a.png", b"png")
    output_dir = tmp_path / "parsed" / "book"
    # No closing backtick anywhere: CommonMark reads it as literal text, so this is an image.
    markdown = "![a `b](a.png)\n"

    document = MarkItDownParser(converter=_FakeConverter(markdown)).parse(source, output_dir)

    assert (output_dir / "book.md").read_text() == "![a `b](images/a.png)\n"
    assert "unresolved_image_count" not in document.metadata


def test_many_images_on_one_line_are_rewritten_in_linear_time(tmp_path: Path) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"pptx")
    output_dir = tmp_path / "parsed" / "deck"
    pictures = [_data_uri(PNG_1X1 + index.to_bytes(4, "big") * 2000) for index in range(300)]
    markdown = " ".join(f"![p{index}]({uri})" for index, uri in enumerate(pictures)) + "\n"

    started = time.perf_counter()
    MarkItDownParser(converter=_FakeConverter(markdown)).parse(source, output_dir)
    elapsed = time.perf_counter() - started

    assert "data:" not in (output_dir / "deck.md").read_text()
    assert len(list((output_dir / "images").iterdir())) == 300
    # Rescanning the rest of the line per image took several seconds here; one pass is far less.
    assert elapsed < 2.0


def test_percent_encoded_data_uri_is_staged(tmp_path: Path) -> None:
    source = tmp_path / "page.html"
    source.write_text("<html/>")
    output_dir = tmp_path / "parsed" / "page"
    svg = "<svg xmlns='http://www.w3.org/2000/svg'/>"
    markdown = f"![icon](data:image/svg+xml,{quote(svg, safe='')})\n"

    document = MarkItDownParser(converter=_FakeConverter(markdown)).parse(source, output_dir)

    [image] = _image_blocks(document)
    assert str(image.metadata["src"]).endswith(".svg")
    assert (output_dir / str(image.metadata["src"])).read_text() == svg
    assert "unresolved_image_count" not in document.metadata


def test_distinct_broken_data_uris_count_separately(tmp_path: Path) -> None:
    source = tmp_path / "report.docx"
    source.write_bytes(b"docx")
    output_dir = tmp_path / "parsed" / "report"
    markdown = "![a](data:image/png;base64,!!!a)\n\n![b](data:image/png;base64,!!!b)\n"

    with pytest.warns(UserWarning, match="2 image reference"):
        document = MarkItDownParser(converter=_FakeConverter(markdown)).parse(source, output_dir)

    assert document.metadata["unresolved_image_count"] == 2


def test_flattened_nested_tables_are_reported(tmp_path: Path) -> None:
    source = tmp_path / "report.docx"
    source.write_bytes(b"docx")
    output_dir = tmp_path / "parsed" / "report"
    converter = _FakeConverter("| a | b |\n| --- | --- |\n| c | x / y |\n", 1)

    with pytest.warns(UserWarning, match="1 nested table"):
        document = MarkItDownParser(converter=converter).parse(source, output_dir)

    assert document.metadata["flattened_table_count"] == 1
