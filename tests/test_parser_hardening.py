from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.cli import app
from bookgraph.parsers.errors import UnsupportedSourceError
from bookgraph.parsers.markdown import MarkdownParser
from bookgraph.parsers.mineru import MinerUMiddleJsonParser
from bookgraph.parsers.routing import select_parser_name
from bookgraph.utils import validate_slug_id


def _init_workspace(tmp_path: Path) -> CliRunner:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return runner


def _failure_text(result: object) -> str:
    output = getattr(result, "output", "") or ""
    return f"{output}\n{getattr(result, 'exception', None) or ''}"


# --- routing: only MinerU middle JSON is auto-routed to the MinerU adapter ---


def test_plain_json_is_not_auto_routed_to_the_mineru_adapter() -> None:
    with pytest.raises(UnsupportedSourceError, match="_middle.json"):
        select_parser_name(Path("config.json"))


def test_mineru_middle_json_is_still_auto_routed() -> None:
    assert select_parser_name(Path("ddia_middle.json")) == "mineru-middle-json"
    assert select_parser_name(Path("DDIA_MIDDLE.JSON")) == "mineru-middle-json"


def test_cli_refuses_plain_json_instead_of_writing_an_empty_document(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    source = tmp_path / "sources" / "inbox" / "config.json"
    source.write_text('{"not": "mineru"}')

    result = runner.invoke(app, ["parse", str(source), "--output", str(tmp_path)])

    assert result.exit_code != 0
    assert "config.json" in _failure_text(result)
    assert not (tmp_path / "sources" / "parsed" / "config").exists()


# --- MinerU payload shape validation ---


def test_mineru_parser_rejects_json_without_pdf_info(tmp_path: Path) -> None:
    source = tmp_path / "config.json"
    source.write_text('{"not": "mineru"}')

    with pytest.raises(UnsupportedSourceError, match="pdf_info"):
        MinerUMiddleJsonParser().parse(source, tmp_path)


def test_mineru_parser_rejects_a_json_array_payload(tmp_path: Path) -> None:
    source = tmp_path / "list.json"
    source.write_text("[1, 2, 3]")

    with pytest.raises(UnsupportedSourceError, match="pdf_info"):
        MinerUMiddleJsonParser().parse(source, tmp_path)


def test_mineru_parser_reports_invalid_json_with_the_filename(tmp_path: Path) -> None:
    source = tmp_path / "broken_middle.json"
    source.write_text("{not json at all")

    with pytest.raises(UnsupportedSourceError, match="broken_middle.json"):
        MinerUMiddleJsonParser().parse(source, tmp_path)


def test_mineru_parser_accepts_a_valid_but_empty_page_list(tmp_path: Path) -> None:
    source = tmp_path / "empty_middle.json"
    source.write_text('{"pdf_info": []}')

    document = MinerUMiddleJsonParser().parse(source, tmp_path)

    assert document.blocks == []
    assert document.doc_id == "empty"


# --- id validation: workspace.md says slugs must not contain path separators ---


@pytest.mark.parametrize(
    "bad_id",
    ["../escape", "a/b", "..", "Upper", "with space", "trailing-", "", "under_score"],
)
def test_validate_slug_id_rejects_unsafe_ids(bad_id: str) -> None:
    with pytest.raises(ValueError, match="slug"):
        validate_slug_id(bad_id, field_name="doc_id")


@pytest.mark.parametrize("good_id", ["deep-work", "ddia", "a1", "a-1-b"])
def test_validate_slug_id_accepts_contract_shaped_slugs(good_id: str) -> None:
    assert validate_slug_id(good_id) == good_id


def test_parse_rejects_a_doc_id_that_escapes_the_workspace(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    source = tmp_path / "sources" / "inbox" / "n.md"
    source.write_text("# T\n\nBody.\n")

    result = runner.invoke(
        app,
        ["parse", str(source), "--output", str(tmp_path), "--doc-id", "../../../escaped"],
    )

    assert result.exit_code != 0
    assert "doc_id" in _failure_text(result)
    assert not (tmp_path.parent / "escaped").exists()


def test_parse_rejects_a_traversing_book_id_from_a_manifest(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    book_root = tmp_path / "sources" / "inbox" / "evil"
    book_root.mkdir(parents=True, exist_ok=True)
    (book_root / "book.json").write_text(json.dumps({"book_id": "../../../escaped"}))
    source = book_root / "original.md"
    source.write_text("# E\n\nBody.\n")

    result = runner.invoke(app, ["parse", str(source), "--output", str(tmp_path)])

    assert result.exit_code != 0
    assert "book_id" in _failure_text(result)
    assert not (tmp_path.parent / "escaped").exists()


# --- MarkItDown provenance: blocks point at the artifact that proves them ---


class _FakeConverter:
    def __init__(self, text_content: str) -> None:
        self._text_content = text_content

    def convert(self, source: str) -> object:
        class _Result:
            text_content = self._text_content

        return _Result()


def test_markitdown_blocks_point_at_the_staged_markdown_not_the_binary(tmp_path: Path) -> None:
    from bookgraph.parsers.markitdown import MarkItDownParser

    source = tmp_path / "Report Q3.docx"
    source.write_bytes(b"binary")
    output_dir = tmp_path / "parsed" / "report-q3"

    document = MarkItDownParser(
        converter=_FakeConverter("# Report Q3\n\nRevenue grew.\n")
    ).parse(source, output_dir)

    staged = output_dir / "report-q3.md"
    assert {block.source_path for block in document.blocks} == {str(staged)}
    assert document.metadata["source_path"] == str(source)
    assert document.metadata["markdown_path"] == str(staged)
    # Line ranges are only meaningful against the staged Markdown.
    assert document.blocks[1].metadata["line_start"] == 3


# --- list structure: numbering and nesting survive ---


def test_ordered_list_keeps_its_numbering(tmp_path: Path) -> None:
    source = tmp_path / "steps.md"
    source.write_text("# T\n\n1. first step\n2. second step\n3. third step\n")

    blocks = MarkdownParser().parse(source, tmp_path / "parsed").blocks

    assert blocks[1].type == "list"
    assert blocks[1].text == "1. first step\n2. second step\n3. third step"


def test_ordered_list_honors_a_non_default_start(tmp_path: Path) -> None:
    source = tmp_path / "steps.md"
    source.write_text("# T\n\n5. fifth\n6. sixth\n")

    blocks = MarkdownParser().parse(source, tmp_path / "parsed").blocks

    assert blocks[1].text == "5. fifth\n6. sixth"


def test_nested_list_keeps_hierarchy_as_indentation(tmp_path: Path) -> None:
    source = tmp_path / "nested.md"
    source.write_text("# T\n\n- outer\n  - inner\n    - deepest\n- second\n")

    blocks = MarkdownParser().parse(source, tmp_path / "parsed").blocks

    assert blocks[1].text == "- outer\n  - inner\n    - deepest\n- second"


def test_mixed_nested_list_keeps_both_markers(tmp_path: Path) -> None:
    source = tmp_path / "mixed.md"
    source.write_text("# T\n\n1. step one\n   - detail a\n   - detail b\n2. step two\n")

    blocks = MarkdownParser().parse(source, tmp_path / "parsed").blocks

    assert blocks[1].text == "1. step one\n  - detail a\n  - detail b\n2. step two"
