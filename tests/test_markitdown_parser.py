from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from bookgraph.parsers.markitdown import MarkItDownParser, MissingParserDependencyError

MARKITDOWN_INSTALLED = importlib.util.find_spec("markitdown") is not None


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


@pytest.mark.skipif(MARKITDOWN_INSTALLED, reason="markitdown extra is installed")
def test_markitdown_parser_reports_missing_optional_dependency(tmp_path: Path) -> None:
    source = tmp_path / "deck.pptx"
    source.write_bytes(b"binary")

    with pytest.raises(MissingParserDependencyError, match="--extra parsers"):
        MarkItDownParser().parse(source, tmp_path / "parsed")
