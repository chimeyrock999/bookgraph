"""Real-MarkItDown regression for EPUBs whose file names need percent-encoding.

MarkItDown < 0.1.8 looked up percent-encoded manifest hrefs (``text/Chapter%20One.xhtml``)
verbatim in the zip, so every chapter or image with a space or non-ASCII name was silently
dropped. These tests run the real converter end to end, so they skip without the ``parsers``
extra (``uv run --extra dev --extra parsers pytest``).
"""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

from bookgraph.parsers.markitdown import MarkItDownParser
from bookgraph.segmenters.heading import HeadingSegmenter

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("markitdown") is None, reason="markitdown extra not installed"
)

FIGURE_ONE = b"\x89PNG figure one"
FIGURE_TWO = b"\x89PNG figure two"

_CONTAINER = (
    '<?xml version="1.0"?>'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
    '<rootfiles><rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/></rootfiles></container>'
)

# Manifest hrefs are percent-encoded as the EPUB spec requires; the zip members below are not.
_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Encoded Names</dc:title><dc:identifier id="id">enc</dc:identifier>
<dc:language>en</dc:language>
</metadata>
<manifest>
<item id="c1" href="text/Chapter%20One.xhtml" media-type="application/xhtml+xml"/>
<item id="c2" href="text/Ch%C6%B0%C6%A1ng%202.xhtml" media-type="application/xhtml+xml"/>
<item id="i1" href="images/Figure%201.png" media-type="image/png"/>
<item id="i2" href="images/H%C3%ACnh%202.png" media-type="image/png"/>
</manifest>
<spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""


def _chapter(title: str, body: str, image_src: str, alt: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>'
        f'<body><h1>{title}</h1><p>{body}</p><img src="{image_src}" alt="{alt}"/></body></html>'
    )


def _make_encoded_name_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", _CONTAINER)
        archive.writestr("OEBPS/content.opf", _OPF)
        archive.writestr(
            "OEBPS/text/Chapter One.xhtml",
            _chapter("Chapter One", "Intro text.", "../images/Figure%201.png", "Figure 1"),
        )
        archive.writestr(
            "OEBPS/text/Chương 2.xhtml",
            _chapter("Chương 2", "Second text.", "../images/H%C3%ACnh%202.png", "Hình 2"),
        )
        archive.writestr("OEBPS/images/Figure 1.png", FIGURE_ONE)
        archive.writestr("OEBPS/images/Hình 2.png", FIGURE_TWO)


def test_real_markitdown_keeps_chapters_with_encoded_names(tmp_path: Path) -> None:
    source = tmp_path / "encoded.epub"
    _make_encoded_name_epub(source)

    document = MarkItDownParser().parse(source, tmp_path / "parsed")
    sections = HeadingSegmenter().segment(document)

    titles = [section.title for section in sections]
    assert "Chapter One" in titles
    assert "Chương 2" in titles
    staged_md = (tmp_path / "parsed" / "encoded.md").read_text()
    assert "Intro text." in staged_md
    assert "Second text." in staged_md


def test_real_markitdown_stages_images_with_encoded_names(tmp_path: Path) -> None:
    source = tmp_path / "encoded.epub"
    _make_encoded_name_epub(source)
    output_dir = tmp_path / "parsed"

    document = MarkItDownParser().parse(source, output_dir)

    # MarkItDown keeps the ``src`` percent-encoded; the staging pass decodes it to find the
    # zip member, so every figure is unpacked and repointed rather than left broken.
    assert "unresolved_image_count" not in document.metadata
    staged_md = (output_dir / "encoded.md").read_text()
    assert "![Figure 1](images/Figure_1.png)" in staged_md
    assert "![Hình 2](images/Hình_2.png)" in staged_md
    assert (output_dir / "images" / "Figure_1.png").read_bytes() == FIGURE_ONE
    assert (output_dir / "images" / "Hình_2.png").read_bytes() == FIGURE_TWO
