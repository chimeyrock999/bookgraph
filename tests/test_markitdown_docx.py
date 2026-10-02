"""DOCX through the real MarkItDown converter: images, merged/nested tables, Title style."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.cli import app
from docx_support import PNG_1X1, cell, image_paragraph, paragraph, table, write_docx

DOCX_SUPPORT = all(importlib.util.find_spec(name) for name in ("markitdown", "mammoth"))
pytestmark = pytest.mark.skipif(not DOCX_SUPPORT, reason="needs the parsers extra (mammoth)")


def _merged_table() -> str:
    return table(
        [
            [cell("Wide header", grid_span=2), cell("h2"), cell("h3")],
            [cell("Tall", v_merge="restart"), cell("r1c1"), cell("r1c2"), cell("r1c3")],
            [cell("", v_merge=""), cell("r2c1"), cell("r2c2"), cell("r2c3")],
            [cell("", v_merge=""), cell("r3c1"), cell("r3c2"), cell("r3c3")],
        ]
    )


def _nested_table() -> str:
    inner = table([[cell("in 00"), cell("in 01")], [cell("in 10"), cell("in 11")]])
    return table([[cell("Outer A"), cell("Outer B")], [cell("Outer C"), cell(inner)]])


def _sample_docx(path: Path) -> Path:
    body = (
        paragraph("Complex Tables Sample", "Title")
        + paragraph("1 Introduction", "Heading1")
        + paragraph("Intro text.")
        + _merged_table()
        + _nested_table()
        + image_paragraph("rIdImg1", "Figure 1")
        + paragraph("After image.")
    )
    return write_docx(path, body, {"rIdImg1": PNG_1X1})


def _parse(tmp_path: Path):  # type: ignore[no-untyped-def]
    from bookgraph.parsers.markitdown import MarkItDownParser

    source = _sample_docx(tmp_path / "sample.docx")
    output_dir = tmp_path / "parsed" / "sample"
    with pytest.warns(UserWarning, match="1 nested table"):
        document = MarkItDownParser().parse(source, output_dir)
    return document, output_dir


def test_docx_embedded_image_is_staged(tmp_path: Path) -> None:
    document, output_dir = _parse(tmp_path)

    [image] = [block for block in document.blocks if block.type == "image"]
    assert image.text == "Figure 1"
    assert (output_dir / str(image.metadata["src"])).read_bytes() == PNG_1X1
    assert "unresolved_image_count" not in document.metadata


def test_docx_rowspan_keeps_columns_aligned(tmp_path: Path) -> None:
    document, _ = _parse(tmp_path)

    merged = next(block for block in document.blocks if "Tall" in block.text)
    rows = [[value.strip() for value in row.split("|")] for row in merged.text.splitlines()]
    assert ["Wide header", "", "h2", "h3"] in rows
    assert ["Tall", "r1c1", "r1c2", "r1c3"] in rows
    assert ["", "r2c1", "r2c2", "r2c3"] in rows
    assert ["", "r3c1", "r3c2", "r3c3"] in rows


def test_docx_nested_table_is_flattened_not_dropped(tmp_path: Path) -> None:
    document, _ = _parse(tmp_path)

    outer = next(block for block in document.blocks if "Outer A" in block.text)
    assert "Outer C | in 00 / in 01; in 10 / in 11" in outer.text
    assert document.metadata["flattened_table_count"] == 1


def test_docx_title_style_becomes_document_title(tmp_path: Path) -> None:
    document, _ = _parse(tmp_path)

    assert document.title == "Complex Tables Sample"
    titles = [(block.text, block.level) for block in document.blocks if block.type == "title"]
    assert titles[:2] == [("Complex Tables Sample", 1), ("1 Introduction", 1)]


def test_parse_and_segment_report_no_asset_warnings_for_docx(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "ws"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    source = _sample_docx(tmp_path / "sample.docx")

    parsed = runner.invoke(app, ["parse", str(source), "-o", str(workspace)])
    assert parsed.exit_code == 0, parsed.output
    assert "title: Complex Tables Sample" in parsed.stdout
    assert "1 nested table(s) were flattened" in parsed.stderr

    segmented = runner.invoke(app, ["segment", str(workspace), "sample"])
    assert segmented.exit_code == 0, segmented.output
    assert "warnings: 0" in segmented.stdout


def test_normalize_tables_pads_trailing_and_wide_rowspans() -> None:
    from bookgraph.parsers.markitdown_docx import normalize_tables

    html = (
        "<table>"
        '<tr><td>a</td><td rowspan="2" colspan="2">wide</td><td rowspan="3">end</td></tr>'
        "<tr><td>b</td></tr>"
        "<tr><td>c</td><td>d</td><td>e</td></tr>"
        "</table>"
    )

    normalized, flattened = normalize_tables(html)

    assert flattened == 0
    assert "rowspan" not in normalized
    assert normalized == (
        "<table>"
        '<tr><td>a</td><td colspan="2">wide</td><td>end</td></tr>'
        '<tr><td>b</td><td colspan="2"></td><td></td></tr>'
        "<tr><td>c</td><td>d</td><td>e</td><td></td></tr>"
        "</table>"
    )
