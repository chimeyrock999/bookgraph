"""EPUB 3 output of a translated reading edition, written with the standard library.

An EPUB is a zip: a ``mimetype`` entry stored first and uncompressed, then
``META-INF/container.xml`` pointing at the package document (``OEBPS/content.opf``),
which lists every file (manifest) and the reading order (spine). The single-page HTML
of the PDF path is the wrong input for it, so :class:`EpubWriter` builds the book from
the edition's structure (:class:`~bookgraph.exports.edition.TranslatedExport`):

- one XHTML file per *chapter* of the outline (the sections that open a new page in
  the PDF); child sections stay inside their chapter's file;
- ``nav.xhtml``, the table of contents, from the outline itself;
- every ``data:`` image written once as a content-addressed file under ``images/``;
- internal links pointed at the file that holds their anchor; a link to nothing in the
  book (an unresolved internal link, a relative link to a file the book does not
  carry) keeps its text but loses its ``href``, since no reading system can follow it;
- every section body re-serialised as well-formed XHTML (:mod:`.xhtml`), reporting
  what had to change as ``xhtml_repaired``;
- ``lang`` on every file's root (the target language) and on original-language
  content (``--source-lang``, else ``und``).

``bilingual`` rows arrive laid out for reflowable screens (the ``interleaved`` layout
of :mod:`.bilingual`). Entries carry the report's ``generated_at`` timestamp, so an
export over unchanged inputs is byte-identical.
"""

from __future__ import annotations

import base64
import hashlib
import re
import uuid
import zipfile
from datetime import datetime
from html import unescape
from pathlib import Path
from urllib.parse import unquote_to_bytes

from bookgraph.exports.bilingual import SIDE_RE
from bookgraph.exports.edition import TranslatedExport
from bookgraph.exports.models import (
    XHTML_REPAIRED,
    ExportReport,
    ExportSection,
    ExportWarning,
    WarningColumn,
)
from bookgraph.exports.outline import OutlineNode
from bookgraph.exports.render import front_matter
from bookgraph.exports.renderers import ExportWriter
from bookgraph.exports.xhtml import to_xhtml, xml_escape

_MIMETYPE = b"application/epub+zip"
_CONTAINER = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b'<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    b'<rootfiles><rootfile full-path="OEBPS/content.opf" '
    b'media-type="application/oebps-package+xml"/></rootfiles>\n'
    b"</container>\n"
)

# The image types an export embeds (see :mod:`.images`), with their file extension.
_IMAGE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/webp": "webp",
}
_DATA_SRC_RE = re.compile(r'(?<=\s)src="data:([^";,]+)((?:;[^";,]*)*),([^"]*)"')
_HREF_RE = re.compile(r'\shref="([^"]*)"')
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_ID_RE = re.compile(r'(?<=\s)id="([^"]*)"')
# Stands for a normalised side of an interleaved pair while the rest is normalised: a
# private-use character no section text carries once its sides are taken out.
_SIDE_SLOT = "\ue000"
_SIDE_SLOT_RE = re.compile(f"{_SIDE_SLOT}(\\d+){_SIDE_SLOT}")
# Zip timestamps cannot go before 1980.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

EPUB_STYLE = """
html { line-height: 1.5; }
h1, h2, h3, h4, h5, h6 { line-height: 1.25; page-break-after: avoid; break-after: avoid; }
.title-page, .frontmatter { margin-top: 20%; text-align: center; }
.frontmatter dl { text-align: left; }
.frontmatter dt { font-weight: bold; }
.notice, .columns-legend { font-size: 0.9em; }
.toc ol { list-style: none; padding-left: 0; }
.toc ol ol { padding-left: 1em; }
.source-note, .placeholder { font-size: 0.85em; font-style: italic; }
.missing-asset { border: 1px dashed; padding: 0.2em 0.4em; font-size: 0.85em; }
figure { margin: 1em 0; text-align: center; }
figure img, p img { max-width: 100%; height: auto; }
figcaption { font-size: 0.85em; margin-top: 0.2em; }
.equation, pre, code { font-family: monospace; }
.equation, pre { white-space: pre-wrap; }
pre, code { font-size: 0.9em; }
table { border-collapse: collapse; margin: 0.5em 0; }
th, td { border: 1px solid #999; padding: 0.15em 0.4em; vertical-align: top; }
.bilingual-unit, .bilingual-section { margin: 0 0 1em; }
.translation { border-left: 3px solid #8aa9c9; padding-left: 0.75em; }
.bilingual-section > .original { margin-bottom: 0.75em; }
@media (min-width: 48em) {
  .bilingual-section { display: flex; gap: 1.5em; }
  .bilingual-section > div { flex: 1 1 0; min-width: 0; }
  .bilingual-section > .original { margin-bottom: 0; }
}
"""


class EpubWriter(ExportWriter):
    name = "epub"
    suffix = ".epub"
    bilingual_layout = "interleaved"

    def available(self) -> bool:
        return True

    def write(self, export: TranslatedExport, output: Path) -> list[ExportWarning]:
        book = _Book(export)
        files = book.files()
        stamp = _zip_timestamp(export.report.generated_at)
        with zipfile.ZipFile(output, "w") as archive:
            _add(archive, "mimetype", _MIMETYPE, stamp, zipfile.ZIP_STORED)
            _add(archive, "META-INF/container.xml", _CONTAINER, stamp)
            for path, data in files:
                _add(archive, f"OEBPS/{path}", data, stamp)
        return book.warnings


class _Book:
    """One export laid out as EPUB files (paths relative to ``OEBPS/``)."""

    def __init__(self, export: TranslatedExport) -> None:
        self.report: ExportReport = export.report
        self.outline = export.outline
        self.bodies = export.bodies
        self.original_sources = export.original_sources
        self.entries: dict[str, ExportSection] = {e.section_id: e for e in self.report.sections}
        self.warnings: list[ExportWarning] = []
        self.images: dict[str, tuple[str, bytes]] = {}  # href → (media type, bytes)

    def files(self) -> list[tuple[str, bytes]]:
        chapters = [
            (f"chapter-{index:03d}.xhtml", node)
            for index, node in enumerate(_chapter_nodes(self.outline), start=1)
        ]
        contents = {name: self._section(node, self.report.lang) for name, node in chapters}
        file_of: dict[str, str] = {}
        for name, content in contents.items():
            for anchor in _ID_RE.findall(content):
                file_of.setdefault(unescape(anchor), name)
        documents = [
            ("title.xhtml", self._document(self.report.title, self._title_page())),
            ("nav.xhtml", self._document("Contents", self._nav(file_of))),
            *(
                (
                    name,
                    self._document(
                        self.entries[node.section.id].title,
                        _point_links(contents[name], name, file_of),
                    ),
                )
                for name, node in chapters
            ),
        ]
        package = self._package([name for name, _ in documents])
        return [
            ("content.opf", package),
            ("style.css", EPUB_STYLE.lstrip().encode()),
            *documents,
            *((href, data) for href, (_, data) in self.images.items()),
        ]

    # -- content documents ----------------------------------------------------------

    def _section(self, node: OutlineNode, context_lang: str) -> str:
        """``node``'s ``<section>``, with its descendants that are not chapters."""

        entry = self.entries[node.section.id]
        lang = self._content_lang(entry, context_lang)
        classes = f"section depth-{node.depth}" + (" chapter" if node.chapter else "")
        if self.report.show_status:
            classes += f" source-{entry.source}"
        attrs = f' lang="{xml_escape(lang)}" xml:lang="{xml_escape(lang)}"'
        children = "".join(
            self._section(child, lang) for child in node.children if not child.chapter
        )
        return (
            f'<section class="{classes}" id="{xml_escape(entry.section_id)}"'
            f"{attrs if lang != context_lang else ''}>"
            f"{self._body(entry)}{children}</section>\n"
        )

    def _content_lang(self, entry: ExportSection, context_lang: str) -> str:
        """The language a section's own content is in.

        In ``bilingual`` mode each side of a row carries its own ``lang``, so a section
        keeps its context's. In ``translated`` mode a fallback is the original text.
        """

        if self.report.mode == "bilingual":
            return context_lang
        return self.report.lang if entry.source == "translated" else self.report.original_lang

    def _body(self, entry: ExportSection) -> str:
        """The section's body as XHTML, its repairs reported per side.

        Each side of an interleaved bilingual pair is normalised on its own, so markup
        broken on one side never spills into the other, and a repair names the side
        (and file) it was made in.
        """

        issues: dict[str, list[str]] = {"original": [], "translation": []}
        sides: list[str] = []

        def take(match: re.Match[str]) -> str:
            xhtml, found = to_xhtml(match.group(2))
            issues[match.group(1)].extend(found)
            sides.append(xhtml)
            return f"{_SIDE_SLOT}{len(sides) - 1}{_SIDE_SLOT}"

        rest, found = to_xhtml(SIDE_RE.sub(take, self.bodies[entry.section_id]))
        issues["translation" if entry.source == "translated" else "original"].extend(found)
        xhtml = _SIDE_SLOT_RE.sub(lambda match: sides[int(match.group(1))], rest)
        for side, repairs in issues.items():
            if repairs:
                self._repaired(entry, side, repairs)
        return _DATA_SRC_RE.sub(self._image_file, xhtml)

    def _repaired(self, entry: ExportSection, side: str, repairs: list[str]) -> None:
        """Report one side's repairs: the translation, or the original text.

        As for other warnings, the original is the ``original`` column only where the
        reading edition does not show it too: a translated or skipped section in
        ``bilingual`` mode.
        """

        column: WarningColumn = "mixed"
        if side == "translation":
            source_path = entry.artifact
        else:
            source_path = self.original_sources.get(entry.section_id)
            if self.report.mode == "bilingual" and entry.source != "original":
                column = "original"
        self.warnings.append(
            ExportWarning(
                code=XHTML_REPAIRED,
                message="changed to be well-formed XHTML: " + "; ".join(dict.fromkeys(repairs)),
                section_id=entry.section_id,
                source_path=source_path,
                column=column,
                origin="translation" if side == "translation" else "source",
            )
        )

    def _image_file(self, match: re.Match[str]) -> str:
        media_type, params, payload = match.group(1).lower(), match.group(2), match.group(3)
        extension = _IMAGE_EXTENSIONS.get(media_type)
        if extension is None:
            return match.group(0)
        payload = unescape(payload)
        if ";base64" in params.lower():
            data = base64.b64decode(payload)
        else:
            data = unquote_to_bytes(payload)
        href = f"images/{hashlib.sha256(data).hexdigest()[:16]}.{extension}"
        self.images.setdefault(href, (media_type, data))
        return f'src="{href}"'

    def _title_page(self) -> str:
        xhtml, _ = to_xhtml(front_matter(self.report, show_status=self.report.show_status))
        return xhtml

    def _nav(self, file_of: dict[str, str]) -> str:
        if self.outline:
            items = self._nav_list(self.outline, file_of)
        else:
            # A nav needs one entry: an empty book still has its title page.
            items = f'<ol><li><a href="title.xhtml">{xml_escape(self.report.title)}</a></li></ol>'
        return f'<nav epub:type="toc" id="toc" class="toc"><h2>Contents</h2>{items}</nav>\n'

    def _nav_list(self, nodes: list[OutlineNode], file_of: dict[str, str]) -> str:
        items = []
        for node in nodes:
            entry = self.entries[node.section.id]
            children = self._nav_list(node.children, file_of) if node.children else ""
            href = f"{file_of[entry.section_id]}#{entry.section_id}"
            items.append(
                f'<li><a href="{xml_escape(href)}">{xml_escape(entry.title)}</a>{children}</li>'
            )
        return "<ol>" + "".join(items) + "</ol>"

    def _document(self, title: str, body: str) -> bytes:
        lang = xml_escape(self.report.lang)
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            f'xmlns:epub="http://www.idpf.org/2007/ops" lang="{lang}" xml:lang="{lang}">\n'
            f'<head>\n<meta charset="utf-8" />\n<title>{xml_escape(title)}</title>\n'
            '<link rel="stylesheet" type="text/css" href="style.css" />\n</head>\n'
            f"<body>\n{body}</body>\n</html>\n"
        ).encode()

    # -- package document -----------------------------------------------------------

    def _package(self, spine: list[str]) -> bytes:
        report = self.report
        identifier = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bookgraph:{report.doc_id}:{report.lang}:{report.mode}"
        )
        languages = [report.lang]
        if report.mode == "bilingual" and report.source_lang not in (None, report.lang):
            languages.append(report.original_lang)
        items = [
            ("nav", "nav.xhtml", "application/xhtml+xml", "nav"),
            ("style", "style.css", "text/css", None),
            *(
                (name.removesuffix(".xhtml"), name, "application/xhtml+xml", None)
                for name in spine
                if name != "nav.xhtml"
            ),
            *(
                (f"img-{Path(href).stem}", href, media_type, None)
                for href, (media_type, _) in self.images.items()
            ),
        ]
        ids = {href: item_id for item_id, href, _, _ in items}
        manifest = "".join(
            f'<item id="{item_id}" href="{xml_escape(href)}" media-type="{media_type}"'
            + (f' properties="{properties}"' if properties else "")
            + "/>\n"
            for item_id, href, media_type, properties in items
        )
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            f'unique-identifier="book-id" xml:lang="{xml_escape(report.lang)}">\n'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'<dc:identifier id="book-id">urn:uuid:{identifier}</dc:identifier>\n'
            f"<dc:title>{xml_escape(report.title)}</dc:title>\n"
            + "".join(f"<dc:language>{xml_escape(lang)}</dc:language>\n" for lang in languages)
            + f'<meta property="dcterms:modified">{xml_escape(report.generated_at)}</meta>\n'
            "</metadata>\n<manifest>\n"
            + manifest
            + "</manifest>\n<spine>\n"
            + "".join(f'<itemref idref="{ids[name]}"/>\n' for name in spine)
            + "</spine>\n</package>\n"
        ).encode()


def _chapter_nodes(nodes: list[OutlineNode]) -> list[OutlineNode]:
    """The nodes that open a file, in reading order (every root is a chapter)."""

    chapters: list[OutlineNode] = []
    for node in nodes:
        if node.chapter:
            chapters.append(node)
        chapters.extend(_chapter_nodes(node.children))
    return chapters


def _point_links(xhtml: str, name: str, file_of: dict[str, str]) -> str:
    """``xhtml`` with each ``#anchor`` link pointed at the file that holds it.

    External URLs stay. Any other link points at nothing in the book (the export
    reported the internal-book ones as ``internal_link_unresolved``) and loses its href.
    """

    def replace(match: re.Match[str]) -> str:
        href = match.group(1)
        if _SCHEME_RE.match(href):
            return match.group(0)
        target = file_of.get(unescape(href[1:])) if href.startswith("#") else None
        if target is None:
            return ""
        if target == name:
            return match.group(0)
        return f' href="{target}{href}"'

    return _HREF_RE.sub(replace, xhtml)


def _zip_timestamp(generated_at: str) -> tuple[int, int, int, int, int, int]:
    try:
        moment = datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return _ZIP_EPOCH
    if moment.year < _ZIP_EPOCH[0]:
        return _ZIP_EPOCH
    return (moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second)


def _add(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    stamp: tuple[int, int, int, int, int, int],
    compression: int = zipfile.ZIP_DEFLATED,
) -> None:
    info = zipfile.ZipInfo(name, date_time=stamp)
    info.compress_type = compression
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)
