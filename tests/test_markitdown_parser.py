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

    staged_asset = output_dir / "images" / "ddia_0206.png"
    assert staged_asset.read_bytes() == figure
    staged_md = (output_dir / "book.md").read_text()
    assert "![Figure 2-6](images/ddia_0206.png)" in staged_md
    image_blocks = [block for block in document.blocks if block.type == "image"]
    assert [block.metadata["src"] for block in image_blocks] == ["images/ddia_0206.png"]
    # The staged src resolves to the real file beside the parsed document.
    assert (output_dir / image_blocks[0].metadata["src"]).read_bytes() == figure


def test_markitdown_parser_repoints_parent_relative_image_refs(tmp_path: Path) -> None:
    figure = b"jpeg bytes"
    source = tmp_path / "book.epub"
    _make_epub(source, {"OEBPS/images/fig.jpg": figure})
    output_dir = tmp_path / "parsed" / "book"
    converter = _FakeConverter("![Fig](../images/fig.jpg)\n")

    MarkItDownParser(converter=converter).parse(source, output_dir)

    assert (output_dir / "images" / "fig.jpg").read_bytes() == figure
    staged_md = (output_dir / "book.md").read_text()
    assert "![Fig](images/fig.jpg)" in staged_md


def test_markitdown_parser_sanitizes_asset_names_with_spaces(tmp_path: Path) -> None:
    figure = b"png bytes"
    source = tmp_path / "book.epub"
    _make_epub(source, {"OEBPS/assets/Diagram One.png": figure})
    output_dir = tmp_path / "parsed" / "book"
    # MarkItDown percent-encodes the space in the href; the staged name must still be link-safe.
    converter = _FakeConverter("![Fig](assets/Diagram%20One.png)\n")

    document = MarkItDownParser(converter=converter).parse(source, output_dir)

    # A space in the destination would tokenise as plain text, so the staged name is link-safe
    # and the reference still round-trips into a resolvable image block.
    assert (output_dir / "images" / "Diagram_One.png").read_bytes() == figure
    image_blocks = [block for block in document.blocks if block.type == "image"]
    assert [block.metadata["src"] for block in image_blocks] == ["images/Diagram_One.png"]
    assert (output_dir / image_blocks[0].metadata["src"]).read_bytes() == figure


def test_markitdown_parser_stages_single_quoted_title_reference(tmp_path: Path) -> None:
    figure = b"png bytes"
    source = tmp_path / "book.epub"
    _make_epub(source, {"OEBPS/assets/fig.png": figure})
    output_dir = tmp_path / "parsed" / "book"
    converter = _FakeConverter("![Fig](assets/fig.png 'a caption')\n")

    MarkItDownParser(converter=converter).parse(source, output_dir)

    assert (output_dir / "images" / "fig.png").read_bytes() == figure
    staged_md = (output_dir / "book.md").read_text()
    assert "![Fig](images/fig.png 'a caption')" in staged_md


def test_markitdown_parser_skips_image_refs_inside_code(tmp_path: Path) -> None:
    figure = b"png bytes"
    source = tmp_path / "book.epub"
    _make_epub(source, {"OEBPS/assets/real.png": figure})
    output_dir = tmp_path / "parsed" / "book"
    converter = _FakeConverter(
        "![Real](assets/real.png)\n\n"
        "Inline example: `![figure](assets/sample.png)` stays literal.\n\n"
        "```markdown\n![fenced](assets/fenced.png)\n```\n"
    )

    MarkItDownParser(converter=converter).parse(source, output_dir)

    staged_md = (output_dir / "book.md").read_text()
    # The real reference is repointed...
    assert "![Real](images/real.png)" in staged_md
    # ...but the code samples are preserved verbatim, and their assets are never staged.
    assert "`![figure](assets/sample.png)`" in staged_md
    assert "![fenced](assets/fenced.png)" in staged_md
    assert not (output_dir / "images" / "sample.png").exists()
    assert not (output_dir / "images" / "fenced.png").exists()


def test_markitdown_parser_stages_bracketed_caption_reference(tmp_path: Path) -> None:
    figure = b"png bytes"
    source = tmp_path / "book.epub"
    _make_epub(source, {"OEBPS/assets/x.png": figure})
    output_dir = tmp_path / "parsed" / "book"
    # A figure number in brackets inside the alt text must not defeat the match.
    converter = _FakeConverter("![Fig [2-6]](assets/x.png)\n")

    document = MarkItDownParser(converter=converter).parse(source, output_dir)

    assert (output_dir / "images" / "x.png").read_bytes() == figure
    image_blocks = [block for block in document.blocks if block.type == "image"]
    assert [block.metadata["src"] for block in image_blocks] == ["images/x.png"]
    assert "unresolved_image_count" not in document.metadata


def test_markitdown_parser_dedups_unresolved_refs_by_normalized_path(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _make_epub(source, {"OEBPS/text/ch01.xhtml": b"<html/>"})
    output_dir = tmp_path / "parsed" / "book"
    # Same missing file, once bare and once with a fragment — one unresolved image, not two.
    converter = _FakeConverter(
        "![A](assets/missing.png)\n\n![B](assets/missing.png#note)\n",
    )

    with pytest.warns(UserWarning):
        document = MarkItDownParser(converter=converter).parse(source, output_dir)

    assert document.metadata["unresolved_image_count"] == 1


def test_markitdown_parser_keeps_old_images_when_extraction_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "book.epub"
    output_dir = tmp_path / "parsed" / "book"
    _make_epub(source, {"OEBPS/assets/old.png": b"good"})
    MarkItDownParser(converter=_FakeConverter("![Old](assets/old.png)\n")).parse(
        source, output_dir
    )
    assert (output_dir / "images" / "old.png").read_bytes() == b"good"

    # Re-parse an EPUB whose referenced member cannot be extracted (simulate a corrupt member
    # / disk error). The parse must not crash, the reference is surfaced as unresolved, and the
    # previous good asset is preserved rather than deleted before the new set is complete.
    import bookgraph.parsers.markitdown as markitdown_module

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise OSError("bad member")

    monkeypatch.setattr(markitdown_module, "_extract_member", _boom)
    _make_epub(source, {"OEBPS/assets/new.png": b"payload"})

    with pytest.warns(UserWarning):
        document = MarkItDownParser(converter=_FakeConverter("![New](assets/new.png)\n")).parse(
            source, output_dir
        )

    assert document.metadata["unresolved_image_count"] == 1
    assert (output_dir / "images" / "old.png").read_bytes() == b"good"
    assert not (output_dir / "images" / "new.png").exists()
    assert not (output_dir / ".images.staging").exists()


def test_markitdown_parser_records_unresolved_image_count(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _make_epub(source, {"OEBPS/assets/there.png": b"png"})
    output_dir = tmp_path / "parsed" / "book"
    converter = _FakeConverter(
        "![Here](assets/there.png)\n\n![Gone](assets/missing.png)\n",
    )

    with pytest.warns(UserWarning):
        document = MarkItDownParser(converter=converter).parse(source, output_dir)

    assert document.metadata["unresolved_image_count"] == 1


def test_markitdown_parser_clears_stale_assets_on_reparse(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    output_dir = tmp_path / "parsed" / "book"
    _make_epub(source, {"OEBPS/assets/old.png": b"old"})
    MarkItDownParser(converter=_FakeConverter("![Old](assets/old.png)\n")).parse(
        source, output_dir
    )
    assert (output_dir / "images" / "old.png").exists()

    # Re-ingest an edited EPUB (same doc_id) whose figure was renamed.
    _make_epub(source, {"OEBPS/assets/new.png": b"new"})
    MarkItDownParser(converter=_FakeConverter("![New](assets/new.png)\n")).parse(
        source, output_dir
    )

    assert (output_dir / "images" / "new.png").read_bytes() == b"new"
    # The orphaned asset from the previous parse is gone, not left as accumulating garbage.
    assert not (output_dir / "images" / "old.png").exists()


def test_markitdown_parser_warns_on_ambiguous_epub_image(tmp_path: Path) -> None:
    source = tmp_path / "book.epub"
    _make_epub(
        source,
        {
            "OEBPS/Text/images/diagram.png": b"chapter one figure",
            "OEBPS/Text/chapter5/images/diagram.png": b"chapter five figure",
        },
    )
    output_dir = tmp_path / "parsed" / "book"
    converter = _FakeConverter("![Fig](images/diagram.png)\n")

    with pytest.warns(UserWarning, match="images/diagram.png"):
        MarkItDownParser(converter=converter).parse(source, output_dir)

    # Rather than guess and show the wrong figure, the ambiguous reference is left untouched.
    staged_md = (output_dir / "book.md").read_text()
    assert "![Fig](images/diagram.png)" in staged_md
    assert not (output_dir / "images").exists()


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
    assert not (output_dir / "images").exists()


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
