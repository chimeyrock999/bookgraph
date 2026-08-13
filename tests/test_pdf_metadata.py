from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.pdf_metadata import PdfBookmark, inspect_pdf_metadata

PdfWriter = pytest.importorskip("pypdf").PdfWriter


def test_inspect_pdf_metadata_reads_title_author_and_flat_bookmarks(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": "Apache Iceberg", "/Author": "Tabular"})
    writer.add_outline_item("Chapter 1", 0)
    writer.add_outline_item("Chapter 2", 1)
    with pdf.open("wb") as handle:
        writer.write(handle)

    metadata = inspect_pdf_metadata(pdf)

    assert metadata.title == "Apache Iceberg"
    assert metadata.author == "Tabular"
    assert metadata.pages == 2
    assert metadata.bookmarks == [
        PdfBookmark(title="Chapter 1", page_index=0, level=1),
        PdfBookmark(title="Chapter 2", page_index=1, level=1),
    ]


def test_inspect_pdf_metadata_preserves_nested_bookmark_levels(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    parent = writer.add_outline_item("Part I", 0)
    writer.add_outline_item("Chapter 1", 1, parent=parent)
    with pdf.open("wb") as handle:
        writer.write(handle)

    metadata = inspect_pdf_metadata(pdf)

    assert metadata.bookmarks == [
        PdfBookmark(title="Part I", page_index=0, level=1),
        PdfBookmark(title="Chapter 1", page_index=1, level=2),
    ]
