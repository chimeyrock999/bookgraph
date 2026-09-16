from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

from bookgraph.parsers.markitdown import MarkItDownParser, MissingParserDependencyError

MARKITDOWN_INSTALLED = importlib.util.find_spec("markitdown") is not None


def _make_epub(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


class _FakeConversion:
    def __init__(self, text_content: str) -> None:
        self.text_content = text_content


class _FakeConverter:
    def __init__(self, text_content: str) -> None:
        self._text_content = text_content
        self.calls: list[str] = []

    def convert(self, source: str) -> _FakeConversion:
        self.calls.append(source)
        return _FakeConversion(self._text_content)


def test_markitdown_parser_stages_converted_markdown_beside_blocks(tmp_path: Path) -> None:
    source = tmp_path / "Report Q3.docx"
    source.write_bytes(b"not really a docx")
    output_dir = tmp_path / "parsed" / "report-q3"
    converter = _FakeConverter("# Report Q3\n\nRevenue grew.\n")

    document = MarkItDownParser(converter=converter).parse(source, output_dir)

    staged = output_dir / "report-q3.md"
    assert converter.calls == [str(source)]
    assert staged.read_text() == "# Report Q3\n\nRevenue grew.\n"
    assert document.doc_id == "report-q3"
    assert document.title == "Report Q3"
    assert [block.type for block in document.blocks] == ["title", "text"]
    assert document.metadata["parser"] == "markitdown"
    assert document.metadata["markdown_path"] == str(staged)
    assert document.metadata["source_path"] == str(source)


def test_markitdown_parser_extracts_epub_image_assets(tmp_path: Path) -> None:
    figure = b"\x89PNG\r\n\x1a\n figure bytes"
    source = tmp_path / "book.epub"
    _make_epub(
        source,
        {
            "META-INF/container.xml": b"<container/>",
            "OEBPS/text/ch02.xhtml": b"<html/>",
            "OEBPS/assets/ddia_0206.png": figure,
        },
    )
    output_dir = tmp_path / "parsed" / "book"
    converter = _FakeConverter(
        "# Book\n\n![Figure 2-6](assets/ddia_0206.png)\n",
    )

    document = MarkItDownParser(converter=converter).parse(source, output_dir)

    staged_asset = output_dir / "assets" / "ddia_0206.png"
    assert staged_asset.read_bytes() == figure
    staged_md = (output_dir / "book.md").read_text()
    assert "![Figure 2-6](assets/ddia_0206.png)" in staged_md
    image_blocks = [block for block in document.blocks if block.type == "image"]
    assert [block.metadata["src"] for block in image_blocks] == ["assets/ddia_0206.png"]
    # The staged src resolves to the real file beside the parsed document.
    assert (output_dir / image_blocks[0].metadata["src"]).read_bytes() == figure


def test_markitdown_parser_repoints_parent_relative_image_refs(tmp_path: Path) -> None:
    figure = b"jpeg bytes"
    source = tmp_path / "book.epub"
    _make_epub(source, {"OEBPS/images/fig.jpg": figure})
    output_dir = tmp_path / "parsed" / "book"
    converter = _FakeConverter("![Fig](../images/fig.jpg)\n")

    MarkItDownParser(converter=converter).parse(source, output_dir)

    assert (output_dir / "assets" / "fig.jpg").read_bytes() == figure
    staged_md = (output_dir / "book.md").read_text()
    assert "![Fig](assets/fig.jpg)" in staged_md


def test_markitdown_parser_warns_on_unresolvable_epub_image(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _make_epub(source, {"OEBPS/text/ch01.xhtml": b"<html/>"})
    output_dir = tmp_path / "parsed" / "book"
    converter = _FakeConverter("![Missing](assets/ghost.png)\n")

    with pytest.warns(UserWarning, match="assets/ghost.png"):
        MarkItDownParser(converter=converter).parse(source, output_dir)

    staged_md = (output_dir / "book.md").read_text()
    # The broken reference is preserved verbatim rather than silently dropped.
    assert "![Missing](assets/ghost.png)" in staged_md
    assert not (output_dir / "assets").exists()


def test_markitdown_parser_leaves_remote_image_refs_untouched(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _make_epub(source, {"OEBPS/images/local.png": b"png"})
    output_dir = tmp_path / "parsed" / "book"
    converter = _FakeConverter("![Remote](https://example.com/pic.png)\n")

    MarkItDownParser(converter=converter).parse(source, output_dir)

    staged_md = (output_dir / "book.md").read_text()
    assert "![Remote](https://example.com/pic.png)" in staged_md


@pytest.mark.skipif(MARKITDOWN_INSTALLED, reason="markitdown extra is installed")
def test_markitdown_parser_reports_missing_optional_dependency(tmp_path: Path) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"binary")

    with pytest.raises(MissingParserDependencyError, match="--extra parsers"):
        MarkItDownParser().parse(source, tmp_path / "parsed")
