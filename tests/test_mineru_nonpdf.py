"""The explicit MinerU path for EPUB/DOCX, against recorded MinerU 4 output.

``fixtures/mineru4/{epub,docx}/`` hold a small EPUB and DOCX (internal and external
links, a figure, a merged and a nested table; the DOCX also has a ``Title``, three
heading levels, a bookmark and a footnote) and the bundle ``mineru-kit parse
--format zip --tier flash`` (MinerU 4.0.10) wrote for each. The tests stage them the
way :class:`MinerURunner` does, source map included.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from bookgraph.models import CanonicalBlock, Document
from bookgraph.parsers.mineru import MinerUMiddleJsonParser
from bookgraph.parsers.mineru_source_map import (
    build_source_map,
    read_source_map,
    write_source_map,
)
from bookgraph.segmenters.heading import HeadingSegmenter

FIXTURES = Path(__file__).parent / "fixtures" / "mineru4"


def _stage(tmp_path: Path, kind: str, *, source_map: bool = True) -> Path:
    recorded = FIXTURES / kind
    parsed_dir = tmp_path / "parsed" / "sample"
    parsed_dir.mkdir(parents=True)
    shutil.copy(recorded / "middle_json.json", parsed_dir / "sample_middle.json")
    shutil.copytree(recorded / "images", parsed_dir / "images")
    if source_map:
        mapping = build_source_map(recorded / f"sample.{kind}", parsed_dir / "images")
        write_source_map(mapping, parsed_dir / "sample_source_map.json")
    return parsed_dir


def _parse(parsed_dir: Path) -> Document:
    return MinerUMiddleJsonParser().parse(parsed_dir / "sample_middle.json", parsed_dir)


def _block(document: Document, block_id: str) -> CanonicalBlock:
    return next(block for block in document.blocks if block.id == block_id)


def test_epub_source_map_lists_the_spine_and_matches_images(tmp_path: Path) -> None:
    parsed_dir = _stage(tmp_path, "epub")

    mapping = read_source_map(parsed_dir / "sample_middle.json")

    assert mapping == {
        "source": "sample.epub",
        "spine": ["OEBPS/text/ch01.xhtml", "OEBPS/text/ch02.xhtml"],
        "images": {"images/page_0_image_2.png": "OEBPS/images/fig-1.png"},
    }


def test_docx_source_map_has_no_spine_and_no_reencoded_image(tmp_path: Path) -> None:
    parsed_dir = _stage(tmp_path, "docx")

    # MinerU re-encodes DOCX pictures as JPEG, so no member has the same bytes.
    assert read_source_map(parsed_dir / "sample_middle.json") == {
        "source": "sample.docx",
        "spine": None,
        "images": {},
    }


def test_source_map_of_an_unreadable_package_only_names_it(tmp_path: Path) -> None:
    broken = tmp_path / "broken.epub"
    broken.write_bytes(b"not a zip")

    assert build_source_map(broken, None) == {"source": "broken.epub", "spine": None, "images": {}}


def test_epub_keeps_links_anchors_tables_and_spine_locators(tmp_path: Path) -> None:
    document = _parse(_stage(tmp_path, "epub"))

    intro = _block(document, "p0.b1")
    # Spans join verbatim and hyperlinks stay Markdown links (no "[ 3 ]" or "( Example").
    assert intro.text == (
        "Read [replication](#epub-a652b1f1fec799e1a325) later, see Figure 1 and "
        "[the site](https://example.com/) (Example 5-2) [3]."
    )
    assert intro.metadata["anchor"] == "epub-60e9ef05336bc7d9323b"
    assert intro.metadata["source_locator"] == "sample.epub!OEBPS/text/ch01.xhtml"
    # Every internal link has a block anchor to land on.
    anchors = {block.metadata.get("anchor") for block in document.blocks}
    assert {"epub-a652b1f1fec799e1a325", "epub-60e9ef05336bc7d9323b"} <= anchors
    replication = _block(document, "p1.b1")
    assert replication.text == "Replication"
    assert replication.metadata["anchor"] == "epub-a652b1f1fec799e1a325"
    assert replication.metadata["source_member"] == "OEBPS/text/ch02.xhtml"

    table = _block(document, "p0.b4")
    assert table.type == "table"
    assert table.asset_path is None
    assert table.text.startswith('<table><tr><th rowspan="2">Region</th><th colspan="2">')
    assert "<td>10<table><tr><td>inner</td></tr></table></td>" in table.text
    assert table.text.endswith("</table>\n\nTable 1. Sales")

    figure = _block(document, "p0.b2")
    assert (figure.type, figure.text, figure.asset_path) == (
        "image",
        "Figure 1. A red box.",
        "images/page_0_image_2.png",
    )
    assert figure.metadata["asset_source_member"] == "OEBPS/images/fig-1.png"
    assert document.metadata["mineru_file_suffix"] == "epub"
    assert document.metadata["source_name"] == "sample.epub"
    assert document.title == "Sample Book"


def test_docx_keeps_merged_and_nested_tables_links_and_bookmarks(tmp_path: Path) -> None:
    document = _parse(_stage(tmp_path, "docx"))

    link = _block(document, "p0.b2")
    assert link.text == (
        "See [the results section](#sec_results) and [the website](https://example.com/) "
        "(Example 5-2) [3]."
    )
    assert _block(document, "p0.b10").metadata["anchor"] == "sec_results"

    merged = _block(document, "p0.b6")
    assert '<td rowspan="2"><p>Region</p></td><td colspan="2"><p>Sales</p></td>' in merged.text
    nested = _block(document, "p0.b8")
    assert "<td><table><tr><td><p>inner A</p></td>" in nested.text

    picture = _block(document, "p0.b9")
    # No caption: the picture's own name is its text.
    assert (picture.text, picture.asset_path) == ("Figure 1", "images/page_0_image_9.jpg")
    assert "asset_source_member" not in picture.metadata
    # A DOCX has no spine: everything is on page 0, so p0.b<index> is the locator.
    assert all(block.page_idx == 0 for block in document.blocks)
    assert all("source_locator" not in block.metadata for block in document.blocks)
    # MinerU drops footnotes (word/footnotes.xml); this pins the known limitation.
    assert not any("footnote body" in block.text for block in document.blocks)


def test_docx_heading_levels_match_the_markitdown_mapping(tmp_path: Path) -> None:
    document = _parse(_stage(tmp_path, "docx"))

    titles = [(block.text, block.level) for block in document.blocks if block.type == "title"]
    # MinerU emits Title=1, Heading 1-3=2-4; MarkItDown maps Title=1, Heading N=N.
    assert titles == [
        ("Sample Report", 1),
        ("Introduction", 1),
        ("Details", 2),
        ("Deep", 3),
        ("Results", 1),
    ]
    sections = HeadingSegmenter(target_level=1).segment(document)
    assert [section.title for section in sections] == ["Sample Report", "Introduction", "Results"]


def test_epub_heading_levels_are_kept(tmp_path: Path) -> None:
    document = _parse(_stage(tmp_path, "epub"))

    titles = [(block.text, block.level) for block in document.blocks if block.type == "title"]
    assert titles == [("Chapter One", 1), ("Chapter Two", 1), ("Replication", 2)]


def test_without_a_source_map_blocks_parse_without_locators(tmp_path: Path) -> None:
    document = _parse(_stage(tmp_path, "epub", source_map=False))

    assert _block(document, "p0.b1").metadata == {
        "mineru_type": "text",
        "anchor": "epub-60e9ef05336bc7d9323b",
    }
    assert "source_name" not in document.metadata


@pytest.mark.parametrize("kind", ["epub", "docx"])
def test_recorded_bundles_parse_every_block(tmp_path: Path, kind: str) -> None:
    document = _parse(_stage(tmp_path, kind))
    raw = json.loads((FIXTURES / kind / "middle_json.json").read_text())

    assert len(document.blocks) == sum(len(page["blocks"]) for page in raw["pages"])
    assert all(block.text for block in document.blocks)


def _epub_with_spine_gap(path: Path) -> Path:
    """A spine ``c1, missing, c3`` whose first href is percent-encoded."""

    opf = (
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>'
        '<item id="c1" href="text/ch%201.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c3" href="text/ch3.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c1"/><itemref idref="missing"/>'
        '<itemref idref="c3"/></spine></package>'
    )
    container = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("META-INF/container.xml", container)
        package.writestr("OEBPS/content.opf", opf)
        package.writestr("OEBPS/text/ch 1.xhtml", "<html/>")
        package.writestr("OEBPS/text/ch3.xhtml", "<html/>")
    return path


def test_spine_keeps_one_entry_per_itemref_and_decodes_hrefs(tmp_path: Path) -> None:
    source = _epub_with_spine_gap(tmp_path / "gap.epub")

    # MinerU makes one page per itemref (an empty one for an unknown idref) and
    # percent-decodes hrefs, so page_idx 2 must still be ch3.
    assert build_source_map(source, None)["spine"] == [
        "OEBPS/text/ch 1.xhtml",
        None,
        "OEBPS/text/ch3.xhtml",
    ]


def test_blocks_after_a_spine_gap_keep_their_own_member(tmp_path: Path) -> None:
    parsed_dir = tmp_path / "parsed" / "gap"
    parsed_dir.mkdir(parents=True)
    source = _epub_with_spine_gap(tmp_path / "gap.epub")
    write_source_map(build_source_map(source, None), parsed_dir / "gap_source_map.json")
    pages = [
        {"page_idx": idx, "blocks": [{"type": "text", "index": 0, "content": [_text(text)]}]}
        for idx, text in ((0, "Para in ch1"), (2, "Para in ch3"))
    ]
    pages.insert(1, {"page_idx": 1, "blocks": []})
    payload = {
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "metadata": {"file_suffix": "epub"},
        "pages": pages,
    }
    (parsed_dir / "gap_middle.json").write_text(json.dumps(payload))

    document = MinerUMiddleJsonParser().parse(parsed_dir / "gap_middle.json", parsed_dir)

    assert [(b.text, b.metadata.get("source_locator")) for b in document.blocks] == [
        ("Para in ch1", "gap.epub!OEBPS/text/ch 1.xhtml"),
        ("Para in ch3", "gap.epub!OEBPS/text/ch3.xhtml"),
    ]


def test_pdf_image_without_caption_keeps_no_text(tmp_path: Path) -> None:
    # A PDF image body holds lettering read inside the figure, not a description.
    image = {
        "type": "image",
        "index": 0,
        "content": [
            {"type": "image_body", "image_path": "images/cover.jpg", "content": "Compliments of"}
        ],
    }
    payload = {
        "schema": "docvortex.middle",
        "schema_version": "2.0",
        "metadata": {"file_suffix": "pdf"},
        "pages": [{"page_idx": 0, "blocks": [image]}],
    }
    source = tmp_path / "b_middle.json"
    source.write_text(json.dumps(payload))

    (block,) = MinerUMiddleJsonParser().parse(source, tmp_path).blocks

    assert (block.text, block.asset_path) == ("", "images/cover.jpg")


def _text(text: str) -> dict[str, str]:
    return {"type": "text", "content": text}
