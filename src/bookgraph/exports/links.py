"""Point a source book's internal links at the assembled export's own anchors.

Parsed text keeps link destinations as the source book wrote them — an EPUB converted
by MarkItDown links ``ch10.html#ch_consistency`` or ``#sec_introduction_distributed`` —
and translations must keep them byte-for-byte (:mod:`bookgraph.translation_structure`).
The export, though, is one document whose sections are anchored on BookGraph section
ids, so those destinations lead nowhere in it. :func:`resolve_internal_links` rewrites
the ``<a href>`` of each rendered section body at export time; the parsed document and
the translation artifacts are never touched.

The source's own anchor ids are not kept by the parsers, so a destination is mapped by
the book's structure, in this order:

1. its fragment is an ``id`` already on the page (a translation or the parsed text
   carried the source anchor) → that anchor;
2. its file names a section of the export (``ch10.html`` → the section titled
   *Chapter 10*; ``app01.html`` → *Appendix A*; ``part02.html`` → *Part II*;
   ``preface.html`` → *Preface*);
3. its fragment's words name one section of that file's subtree (``sec_x_y`` → the one
   section titled exactly *x y*, else the one whose title has the words *x* and *y*;
   words of the chapter's own title may be left out, as in
   ``ch10.html#sec_consistency_linearizability`` → *Linearizability*). A
   fragment-only link (``#sec_x``) points into its own source file, so it is looked up
   in the linking section's chapter first, then the book;
4. a file that names a section but a fragment that does not (a figure, an example)
   → the file's section, the deterministic container of the target.

Anything else is left as written and reported (``internal_link_unresolved``). Remote
URLs (``https:``, ``mailto:``, …), absolute paths and links to non-HTML files
(images, PDFs) are not internal-book links and are never rewritten.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from html import escape, unescape
from pathlib import PurePosixPath
from urllib.parse import unquote

from bookgraph.exports.html_attrs import HTML_ATTR_RE, HTML_START_TAG_RE
from bookgraph.exports.models import (
    INTERNAL_LINK_UNRESOLVED,
    ExportWarning,
    WarningColumn,
    WarningOrigin,
)
from bookgraph.exports.outline import OutlineNode, flatten

# File types of a source book's own documents (EPUB/HTML book chapters).
_BOOK_DOCUMENT_SUFFIXES = frozenset({".html", ".htm", ".xhtml"})
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

# Source file names: ``ch10``, ``chapter-3``, ``ch10s02`` (a split chapter), ``app01`` /
# ``appendix-b``, ``part02``.
_CHAPTER_FILE = re.compile(r"ch(?:ap(?:ter)?)?[-_]?0*(\d+)(?:s\d+)?")
_APPENDIX_FILE = re.compile(r"app(?:endix)?[-_]?0*(\d+|[a-z])")
_PART_FILE = re.compile(r"part[-_]?0*(\d+)")

# Leading fragment words that say what kind of target it is, not what it is called.
_SECTION_PREFIXES = frozenset({"ch", "chapter", "sec", "section", "part", "app", "appendix"})
# Fragments of elements inside a section: they never name a section of their own.
_ELEMENT_PREFIXES = frozenset(
    {"fig", "figure", "tab", "table", "ex", "example", "eq", "equation", "fn", "footnote"}
)
_ROMAN = ((10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))


class InternalLinks:
    """Resolves the internal-book links of one export against its page's anchors.

    ``bodies`` are the rendered section bodies that keep their anchors (in ``bilingual``
    mode the mixed column only: the original column's ids are stripped). Every section
    id of ``outline`` is an anchor too.
    """

    def __init__(self, outline: list[OutlineNode], bodies: Iterable[str]) -> None:
        anchors = _page_anchors(bodies) | {node.section.id for node in flatten(outline)}
        self._resolver = _Resolver(outline, anchors)

    def rewrite(self, html: str, section_id: str) -> tuple[str, list[str]]:
        """``html`` with its internal-book ``<a href>`` pointed at export anchors.

        Also returns the destinations left as written (no anchor matches them), once
        each, in the order they appear.
        """

        missing: list[str] = []
        html = _rewrite_hrefs(html, section_id, self._resolver, missing)
        return html, list(dict.fromkeys(missing))


def unresolved_link_warning(
    section_id: str,
    href: str,
    source_path: str | None,
    *,
    column: WarningColumn,
    origin: WarningOrigin,
) -> ExportWarning:
    """The report entry for a link :meth:`InternalLinks.rewrite` left as written."""

    return ExportWarning(
        code=INTERNAL_LINK_UNRESOLVED,
        message=f"internal link '{href}' matches no section or anchor of this export; "
        "left as written",
        section_id=section_id,
        reference=href,
        source_path=source_path,
        column=column,
        origin=origin,
    )


def _rewrite_hrefs(html: str, section_id: str, resolver: _Resolver, missing: list[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group("comment") or match.group("name").lower() != "a":
            return match.group(0)
        attrs = match.group("attrs")
        for attr in HTML_ATTR_RE.finditer(attrs):
            if attr.group(1).lower() != "href" or attr.group(2) is None:
                continue
            href = unescape(attr.group(2).strip("\"'"))
            if not _is_internal(href):
                return match.group(0)
            target = resolver.resolve(href, section_id)
            if target is None:
                missing.append(href)
                return match.group(0)
            new = f'href="#{escape(target, quote=True)}"'
            attrs = attrs[: attr.start()] + new + attrs[attr.end() :]
            return f"<{match.group('name')}{attrs}{match.group('end')}"
        return match.group(0)

    return HTML_START_TAG_RE.sub(replace, html)


def _is_internal(href: str) -> bool:
    """Whether ``href`` points into the source book (a fragment or a book document)."""

    if not href or _SCHEME_RE.match(href) or href.startswith(("/", "\\")):
        return False
    path, _, fragment = href.partition("#")
    path = path.split("?", 1)[0]
    if not path:
        return bool(fragment)
    return PurePosixPath(unquote(path)).suffix.lower() in _BOOK_DOCUMENT_SUFFIXES


def _page_anchors(bodies: Iterable[str]) -> set[str]:
    """Every ``id`` (and ``name`` on ``<a>``) on the page's section bodies."""

    anchors: set[str] = set()
    for body in bodies:
        for match in HTML_START_TAG_RE.finditer(body):
            if match.group("comment"):
                continue
            names = {"id", "name"} if match.group("name").lower() == "a" else {"id"}
            for attr in HTML_ATTR_RE.finditer(match.group("attrs")):
                if attr.group(1).lower() in names and attr.group(2) is not None:
                    anchors.add(unescape(attr.group(2).strip("\"'")))
    return anchors


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _roman(number: int) -> str:
    out = ""
    for value, numeral in _ROMAN:
        while number >= value:
            out += numeral
            number -= value
    return out


class _Resolver:
    def __init__(self, outline: list[OutlineNode], anchors: set[str]) -> None:
        self._anchors = anchors
        self._nodes = flatten(outline)
        self._order = {node.section.id: index for index, node in enumerate(self._nodes)}
        # The chapter each section reads in: its nearest chapter ancestor (or itself),
        # else its top-level ancestor.
        self._chapter: dict[str, OutlineNode] = {}
        for root in outline:
            self._assign_chapters(root, root)

    def _assign_chapters(self, node: OutlineNode, chapter: OutlineNode) -> None:
        chapter = node if node.chapter else chapter
        self._chapter[node.section.id] = chapter
        for child in node.children:
            self._assign_chapters(child, chapter)

    def resolve(self, href: str, section_id: str) -> str | None:
        path, _, fragment = href.partition("#")
        fragment = unquote(fragment)
        if fragment in self._anchors:
            return fragment
        path = path.split("?", 1)[0]
        if path:
            document = self._document_node(PurePosixPath(unquote(path)).stem.lower())
            if document is None:
                return None
            if fragment:
                named = self._fragment_node(fragment, document)
                if named is not None:
                    return named.section.id
            return document.section.id
        chapter = self._chapter.get(section_id)
        named = self._fragment_node(fragment, chapter) if chapter is not None else None
        if named is None:
            named = self._fragment_node(fragment, None)
        return named.section.id if named is not None else None

    def _document_node(self, stem: str) -> OutlineNode | None:
        """The section a source file's name stands for (``ch10`` → *Chapter 10*)."""

        if match := _CHAPTER_FILE.fullmatch(stem):
            number = match.group(1)
            title = re.compile(rf"^(?:chapter\s+0*{number}\b|0*{number}(?:[.:)]|\s))")
        elif match := _APPENDIX_FILE.fullmatch(stem):
            key = match.group(1)
            letter = chr(ord("a") + int(key) - 1) if key.isdigit() and 0 < int(key) <= 26 else key
            title = re.compile(rf"^appendix\s+(?:{letter}|0*{key})\b")
        elif match := _PART_FILE.fullmatch(stem):
            number = int(match.group(1))
            title = re.compile(rf"^part\s+(?:0*{number}|{_roman(number)})\b")
        else:
            words = _words(stem)
            return self._shallowest(
                node for node in self._nodes if words and _words(node.section.title) == words
            )
        return self._shallowest(
            node for node in self._nodes if title.match(node.section.title.strip().lower())
        )

    def _fragment_node(self, fragment: str, scope: OutlineNode | None) -> OutlineNode | None:
        """The one section of ``scope`` (``None``: the book) a fragment's words name."""

        words = _words(fragment)
        if words and words[0] in _ELEMENT_PREFIXES:
            return None
        if words and words[0] in _SECTION_PREFIXES:
            words = words[1:]
        nodes = list(scope.walk()) if scope is not None else self._nodes
        topic: set[str] = set()
        if scope is not None:
            # A fragment may repeat its chapter's topic (``sec_consistency_x`` in
            # *Consistency and Consensus*); those words alone name the chapter.
            topic = set(_words(self._chapter[scope.section.id].section.title))
            words = [word for word in words if word not in topic]
            if not words:
                return scope
        if not words:
            return None
        # A title with exactly the fragment's words (``sec_indexes`` → *Indexes*) wins
        # over titles that only contain them (*Transactions and Indexes*), at any depth.
        wanted = set(words)
        titles = [(node, set(_words(node.section.title)) - topic) for node in nodes]
        exact = [node for node, title in titles if title == wanted]
        if exact:
            return self._shallowest(exact)
        return self._shallowest(node for node, title in titles if wanted <= title)

    def _shallowest(self, nodes: Iterable[OutlineNode]) -> OutlineNode | None:
        """The one match at the shallowest depth; ``None`` when none, or several, match."""

        matches = sorted(nodes, key=lambda node: (node.depth, self._order[node.section.id]))
        if not matches or (len(matches) > 1 and matches[1].depth == matches[0].depth):
            return None
        return matches[0]
