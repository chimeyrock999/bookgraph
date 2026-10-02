"""EPUB 3 output of ``bookgraph export translated-pdf`` (``--renderer epub`` / ``.epub``)."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from bookgraph.documents import write_document
from bookgraph.exports.epub import EpubWriter
from bookgraph.exports.models import ExportReport
from bookgraph.exports.translated import (
    build_translated_export,
    report_path_for,
    write_translated_export,
)
from bookgraph.mcp import service
from bookgraph.models import Section, TranslationUnit
from bookgraph.parsers.markdown import MarkdownParser
from bookgraph.sections import write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.translations import write_translation
from bookgraph.workspace import WorkspacePaths
from translated_export_support import DOC, GENERATED_AT, PNG, _register

CHAPTER = "tiny.chapter-one"
_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "x": "http://www.w3.org/1999/xhtml",
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
}
_EPUB_NS = "{http://www.idpf.org/2007/ops}"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _write_epub(
    workspace: WorkspacePaths, out: Path, doc_id: str = DOC, **options: object
) -> tuple[dict[str, bytes], ExportReport]:
    export = build_translated_export(
        workspace,
        doc_id,
        lang="vi",
        generated_at=GENERATED_AT,
        bilingual_layout="interleaved",
        **options,  # type: ignore[arg-type]
    )
    report = write_translated_export(export, out, EpubWriter())
    with zipfile.ZipFile(out) as archive:
        return {name: archive.read(name) for name in archive.namelist()}, report


def _text(files: dict[str, bytes], name: str) -> str:
    return files[f"OEBPS/{name}"].decode("utf-8")


def _opf(files: dict[str, bytes]) -> ET.Element:
    return ET.fromstring(files["OEBPS/content.opf"])


def _manifest(files: dict[str, bytes]) -> dict[str, ET.Element]:
    return {item.get("id", ""): item for item in _opf(files).iterfind("opf:manifest/opf:item", _NS)}


def _spine(files: dict[str, bytes]) -> list[str]:
    manifest = _manifest(files)
    return [
        manifest[ref.get("idref", "")].get("href", "")
        for ref in _opf(files).iterfind("opf:spine/opf:itemref", _NS)
    ]


def _nav_links(files: dict[str, bytes]) -> list[str]:
    nav = ET.fromstring(files["OEBPS/nav.xhtml"])
    toc = next(n for n in nav.iter(f"{{{_NS['x']}}}nav") if n.get(f"{_EPUB_NS}type") == "toc")
    return [a.get("href", "") for a in toc.iter(f"{{{_NS['x']}}}a")]


def _markdown_book(tmp_path: Path) -> tuple[WorkspacePaths, list[Section]]:
    """A three-chapter Markdown book through the real parse → segment pipeline."""

    source = tmp_path / "book.md"
    source.write_text(
        "# Chapter One\n\nOpening prose.\n\n## Details\n\nDetail prose.\n\n"
        "# Chapter Two\n\nSecond chapter prose.\n\n# Chapter Three\n\nThird chapter prose.\n",
        encoding="utf-8",
    )
    workspace = WorkspacePaths(tmp_path / "ws")
    document = MarkdownParser().parse(source, workspace.sources_parsed / "book")
    document = document.model_copy(update={"doc_id": "book", "title": "Markdown Book"})
    write_document(document, workspace.sources_parsed / "book")
    sections = HeadingSegmenter(target_level=2).segment(document)
    write_sections(sections, workspace.sources_sections / "book")
    return workspace, sections


# -- container -----------------------------------------------------------------------


def test_the_container_starts_with_a_stored_mimetype(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    out = tmp_path / "book.epub"
    files, report = _write_epub(workspace, out)

    with zipfile.ZipFile(out) as archive:
        first = archive.infolist()[0]
    assert first.filename == "mimetype"
    assert first.compress_type == zipfile.ZIP_STORED
    assert files["mimetype"] == b"application/epub+zip"
    container = ET.fromstring(files["META-INF/container.xml"])
    rootfile = container.find("c:rootfiles/c:rootfile", _NS)
    assert rootfile is not None
    assert rootfile.get("full-path") == "OEBPS/content.opf"
    assert rootfile.get("media-type") == "application/oebps-package+xml"
    assert report.renderer == "epub"
    assert report_path_for(out).is_file()


def test_the_package_lists_every_file_and_reads_title_nav_chapters(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    files, _ = _write_epub(workspace, tmp_path / "book.epub")

    opf = _opf(files)
    assert opf.get("version") == "3.0"
    assert opf.findtext("opf:metadata/dc:title", namespaces=_NS) == "Tiny Book"
    assert [e.text for e in opf.iterfind("opf:metadata/dc:language", _NS)] == ["vi"]
    identifier = opf.findtext("opf:metadata/dc:identifier", namespaces=_NS) or ""
    assert identifier.startswith("urn:uuid:")
    modified = opf.find("opf:metadata/opf:meta[@property='dcterms:modified']", _NS)
    assert modified is not None and modified.text == GENERATED_AT
    manifest = _manifest(files)
    hrefs = {item.get("href") for item in manifest.values()}
    assert hrefs == {name.removeprefix("OEBPS/") for name in files if name.startswith("OEBPS/")} - {
        "content.opf"
    }
    nav = next(item for item in manifest.values() if item.get("href") == "nav.xhtml")
    assert nav.get("properties") == "nav"
    assert _spine(files) == ["title.xhtml", "nav.xhtml", "chapter-001.xhtml"]
    for name, data in files.items():
        if name.endswith((".xhtml", ".opf", ".xml")):
            ET.fromstring(data)  # every document is well-formed XML


def test_images_become_content_addressed_files(workspace: WorkspacePaths, tmp_path: Path) -> None:
    files, _ = _write_epub(workspace, tmp_path / "book.epub")

    image = f"images/{hashlib.sha256(PNG).hexdigest()[:16]}.png"
    assert files[f"OEBPS/{image}"] == PNG
    assert _manifest(files)  # listed with its media type:
    assert any(
        item.get("href") == image and item.get("media-type") == "image/png"
        for item in _manifest(files).values()
    )
    chapter = _text(files, "chapter-001.xhtml")
    assert f'<img src="{image}"' in chapter
    assert all("data:" not in data.decode("utf-8", "replace") for data in files.values())


def test_writing_twice_gives_identical_bytes(workspace: WorkspacePaths, tmp_path: Path) -> None:
    _write_epub(workspace, tmp_path / "a.epub")
    _write_epub(workspace, tmp_path / "b.epub")

    assert (tmp_path / "a.epub").read_bytes() == (tmp_path / "b.epub").read_bytes()


# -- chapters, nav, links ------------------------------------------------------------


def test_each_chapter_is_a_file_and_the_nav_follows_the_outline(tmp_path: Path) -> None:
    workspace, sections = _markdown_book(tmp_path)
    one, details, two, three = (section.id for section in sections)

    files, _ = _write_epub(workspace, tmp_path / "book.epub", doc_id="book")

    assert _spine(files) == [
        "title.xhtml",
        "nav.xhtml",
        "chapter-001.xhtml",
        "chapter-002.xhtml",
        "chapter-003.xhtml",
    ]
    first = _text(files, "chapter-001.xhtml")
    assert f'id="{one}"' in first and f'id="{details}"' in first  # child stays inside
    assert "Detail prose." in first and "Second chapter prose." not in first
    assert f'id="{two}"' in _text(files, "chapter-002.xhtml")
    assert _nav_links(files) == [
        f"chapter-001.xhtml#{one}",
        f"chapter-001.xhtml#{details}",
        f"chapter-002.xhtml#{two}",
        f"chapter-003.xhtml#{three}",
    ]
    nav = _text(files, "nav.xhtml")
    # The details entry is nested under chapter one.
    assert re.search(rf'#{re.escape(one)}">Chapter One</a><ol><li><a [^>]*>Details</a>', nav)


def test_links_point_at_the_file_that_holds_their_anchor(tmp_path: Path) -> None:
    workspace, sections = _markdown_book(tmp_path)
    one, details, two, _ = (section.id for section in sections)
    write_translation(
        workspace,
        sections[0],
        "vi",
        f"# Chương Một\n\nXem [chương hai](#{two}) và [chi tiết](#{details}).\n",
    )

    files, _ = _write_epub(workspace, tmp_path / "book.epub", doc_id="book")

    first = _text(files, "chapter-001.xhtml")
    assert f'<a href="chapter-002.xhtml#{two}">chương hai</a>' in first
    assert f'<a href="#{details}">chi tiết</a>' in first  # same file: stays a fragment


def test_links_to_nothing_in_the_book_keep_their_text_only(tmp_path: Path) -> None:
    workspace, sections = _markdown_book(tmp_path)
    write_translation(
        workspace,
        sections[0],
        "vi",
        "# Chương Một\n\nXem [hình](#fig_nowhere), [thuật ngữ](glossary01.html#x), "
        "[tệp](notes.pdf) và [trang](https://example.com/a#b).\n",
    )

    files, report = _write_epub(workspace, tmp_path / "book.epub", doc_id="book")

    first = _text(files, "chapter-001.xhtml")
    # A reading system cannot follow them: an EPUB link must land in the package.
    assert "<a>hình</a>" in first and "<a>thuật ngữ</a>" in first and "<a>tệp</a>" in first
    assert '<a href="https://example.com/a#b">trang</a>' in first
    unresolved = {w.reference for w in report.warnings if w.code == "internal_link_unresolved"}
    assert unresolved == {"#fig_nowhere", "glossary01.html#x"}  # still reported


def test_html_the_epub_cannot_carry_is_repaired_and_reported(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    _register(
        workspace,
        "tiny.section-two",
        '## Phần Hai\n\n<div><span onclick="x()">mở</div></p>\n\n<script>bad()</script>\n',
    )

    files, report = _write_epub(workspace, tmp_path / "book.epub")

    chapter = _text(files, "chapter-001.xhtml")
    ET.fromstring(chapter)
    assert "<script" not in chapter and "onclick" not in chapter
    (warning,) = [w for w in report.warnings if w.code == "xhtml_repaired"]
    assert warning.section_id == "tiny.section-two"
    assert warning.origin == "translation"
    assert "dropped element <script>" in warning.message
    written = ExportReport.model_validate_json(report_path_for(tmp_path / "book.epub").read_text())
    assert [w.code for w in written.warnings].count("xhtml_repaired") == 1


# -- languages -----------------------------------------------------------------------


def test_root_and_original_language_content_carry_lang(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    _register(workspace, "tiny.section-two", "## Phần Hai\n\nVăn bản.\n")

    files, report = _write_epub(workspace, tmp_path / "book.epub", source_lang="EN")

    root = ET.fromstring(files["OEBPS/chapter-001.xhtml"])
    assert (root.get("lang"), root.get(_XML_LANG)) == ("vi", "vi")
    sections = {s.get("id"): s for s in root.iter(f"{{{_NS['x']}}}section")}
    assert sections["tiny.chapter-one"].get("lang") == "en"  # untranslated: the original
    assert sections["tiny.chapter-one"].get(_XML_LANG) == "en"
    # A translated section inside an untranslated one switches back; an untranslated
    # one inherits its parent's language.
    assert sections["tiny.section-two"].get("lang") == "vi"
    assert sections["tiny.section-three"].get("lang") is None
    assert report.source_lang == "en"


# -- bilingual layout ----------------------------------------------------------------


def test_aligned_translation_is_interleaved_unit_by_unit(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    service.write_section_translation(
        workspace,
        DOC,
        CHAPTER,
        "vi",
        units=[
            TranslationUnit(source_block_ids=["b0", "b1"], content="# Chương Một\n\nMở đầu."),
            TranslationUnit(source_block_ids=["b3"], content="Văn bản sau hình."),
        ],
    )

    files, _ = _write_epub(workspace, tmp_path / "book.epub", mode="bilingual", source_lang="en")

    chapter = _text(files, "chapter-001.xhtml")
    assert '<table class="bilingual">' not in chapter
    original = '<div class="original" lang="en" xml:lang="en">'
    translation = '<div class="translation" lang="vi" xml:lang="vi">'
    assert re.search(
        rf'<div class="bilingual-unit">{original}<p>Intro prose in English.</p>\s*</div>'
        rf"{translation}<p>Mở đầu.</p>\s*</div></div>",
        chapter,
    )
    # The figure no unit translates is an original block alone, before the next unit.
    assert re.search(
        rf'<div class="bilingual-unit">{original}<figure class="asset image">.*?</figure></div>'
        rf'</div><div class="bilingual-unit">{original}<p>Prose after the figure.</p>\s*</div>'
        rf"{translation}<p>Văn bản sau hình.</p>\s*</div></div>",
        chapter,
    )
    opf = _opf(files)
    assert [e.text for e in opf.iterfind("opf:metadata/dc:language", _NS)] == ["vi", "en"]


def test_unaligned_translation_stacks_original_before_translation(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    _register(workspace, "tiny.section-three", "## Phần Ba\n\nVăn bản phần ba.\n")

    files, _ = _write_epub(workspace, tmp_path / "book.epub", mode="bilingual")

    chapter = _text(files, "chapter-001.xhtml")
    match = re.search(
        r'<section [^>]*id="tiny.section-three"[^>]*><div class="bilingual-section">'
        r'<div class="original" lang="und" xml:lang="und">(.*?)</div>'
        r'<div class="translation" lang="vi" xml:lang="vi">(.*?)</div></div>',
        chapter,
        re.S,
    )
    assert match is not None
    assert "Third section English text." in match.group(1)
    assert "Văn bản phần ba." in match.group(2)
    assert "@media (min-width: 48em)" in _text(files, "style.css")


def test_untranslated_section_shows_the_original_once(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    files, _ = _write_epub(workspace, tmp_path / "book.epub", mode="bilingual")

    chapter = _text(files, "chapter-001.xhtml")
    assert chapter.count("Third section English text.") == 1
    assert '<div class="translation"' not in chapter


def test_html_bilingual_rows_tag_the_source_language(workspace: WorkspacePaths) -> None:
    export = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT, source_lang="en"
    )

    assert '<td class="column column-original" data-column="original" lang="en">' in export.html
    assert '<table class="bilingual">' in export.html  # the PDF/HTML layout is unchanged
