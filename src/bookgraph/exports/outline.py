"""The book structure an export is assembled into: reading order, depth, chapters.

The sections manifest alone is not always the book's structure. MinerU marks every
title block as level 1, so a heading-segmented PDF comes out flat (every section a
top-level "part"), and manifests written before the bookmark segmenter kept outline
order on same-page ties list those siblings alphabetically. When the source PDF has an
outline (``sources/inbox/<doc_id>/book.json``), it is the canonical table of contents:
sections are matched to bookmarks by title (on a page the section covers, when both
pages are known), take their depth from the bookmark level, and are put in outline
order. Sections no bookmark names (a MinerU sub-heading, say) travel with the matched
section before them, one level below it. Without an outline, or when no title
matches, the manifest order and ``Section.level`` are kept.

A *chapter* starts a new page: every top-level node, and every child of a top-level
node that is a *part*. A part is recognised by its title ("Part I", "Book 2",
"Volume III"), or by its shape: at least two children in the outline (or, without
one, the manifest), each with children of its own, and little body of its own. Shape
is decided once for the whole book: it counts only when most top-level nodes that
have children share it, and never for a node titled as a chapter ("Chapter 3"). A
chapter whose sections happen to have subsections is then not mistaken for a part
just because its own intro is short. A section
whose bookmark sits directly under a bookmark titled as a part is a chapter too, even
when the part itself has no section. Everything else flows inside its chapter.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from bookgraph.models import Section
from bookgraph.pdf_metadata import PdfBookmark

_MAX_DEPTH = 6

# A bookmark page may sit one page off the first block the parser put on it.
_PAGE_TOLERANCE = 1

# A part-title page carries at most a short introduction.
_PART_MAX_WORDS = 300

_NUMERAL = r"(\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b"
_CHAPTER_TITLE = re.compile(r"^chapter\s+" + _NUMERAL)
_PART_TITLE = re.compile(r"^(part|book|volume)\s+" + _NUMERAL)


@dataclass
class OutlineNode:
    """One section placed in the export's structure.

    ``depth`` is the heading level the section renders at (1–6); ``chapter`` means it
    opens a new page.
    """

    section: Section
    depth: int
    chapter: bool = False
    children: list[OutlineNode] = field(default_factory=list)

    def walk(self) -> Iterator[OutlineNode]:
        yield self
        for child in self.children:
            yield from child.walk()


def build_outline(sections: list[Section], bookmarks: list[PdfBookmark]) -> list[OutlineNode]:
    """Arrange ``sections`` into the export's tree (roots in reading order)."""

    anchors = _match_bookmarks(sections, bookmarks)
    if anchors:
        placed = _outline_order(sections, bookmarks, anchors)
    else:
        placed = [(section, _clamp(section.level), -1) for section in sections]
    parents, outline_children = _bookmark_tree(bookmarks)
    roots: list[OutlineNode] = []
    stack: list[OutlineNode] = []
    root_anchor: dict[int, int] = {}
    for section, depth, anchor in placed:
        parent = parents[anchor] if anchor >= 0 else -1
        node = OutlineNode(
            section=section,
            depth=depth,
            chapter=parent >= 0 and _has_part_title(bookmarks[parent].title),
        )
        while stack and stack[-1].depth >= depth:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
            root_anchor[id(node)] = anchor
        stack.append(node)
    shaped = {
        id(root) for root in roots if _has_part_shape(root, root_anchor[id(root)], outline_children)
    }
    # Most top-level nodes with children share the part shape: the book is in parts.
    book_in_parts = 2 * len(shaped) > sum(1 for root in roots if root.children)
    for root in roots:
        root.chapter = True
        if _has_part_title(root.section.title) or (
            book_in_parts
            and id(root) in shaped
            and not _CHAPTER_TITLE.match(_normalise(root.section.title))
        ):
            for child in root.children:
                child.chapter = True
    return roots


def flatten(roots: list[OutlineNode]) -> list[OutlineNode]:
    """Every node in reading order (pre-order)."""

    return [node for root in roots for node in root.walk()]


def _match_bookmarks(sections: list[Section], bookmarks: list[PdfBookmark]) -> dict[int, int]:
    """Section index → bookmark index, for sections whose title a bookmark carries.

    When the section and a bookmark both know their page, the bookmark must point into
    the section's page span: a heading the outline does not list (a Preface's
    "Overview") never takes a same-titled bookmark from another chapter. Repeated
    titles ("Conclusion" closes every chapter) go to the candidate on the nearest page,
    else to the next one after the previous match in outline order.
    """

    by_title: dict[str, list[int]] = {}
    for index, bookmark in enumerate(bookmarks):
        by_title.setdefault(_normalise(bookmark.title), []).append(index)
    anchors: dict[int, int] = {}
    used: set[int] = set()
    cursor = -1
    for index, section in enumerate(sections):
        candidates = [
            b
            for b in by_title.get(_normalise(section.title), [])
            if b not in used and _on_section_pages(bookmarks[b], section)
        ]
        if not candidates:
            continue
        chosen = min(candidates, key=lambda b: _rank(bookmarks[b], b, section.page_start, cursor))
        anchors[index] = chosen
        used.add(chosen)
        cursor = chosen
    return anchors


def _on_section_pages(bookmark: PdfBookmark, section: Section) -> bool:
    if bookmark.page_index is None or section.page_start is None:
        return True
    end = section.page_end if section.page_end is not None else section.page_start
    first, last = min(section.page_start, end), max(section.page_start, end)
    return first - _PAGE_TOLERANCE <= bookmark.page_index <= last + _PAGE_TOLERANCE


def _rank(bookmark: PdfBookmark, index: int, page: int | None, cursor: int) -> tuple[int, int, int]:
    """Candidate order: nearest page, then after the previous match, then outline order."""

    if page is not None and bookmark.page_index is not None:
        distance = abs(bookmark.page_index - page)
    else:
        distance = 1 << 30
    return distance, 0 if index > cursor else 1, index


def _outline_order(
    sections: list[Section], bookmarks: list[PdfBookmark], anchors: dict[int, int]
) -> list[tuple[Section, int, int]]:
    """``(section, depth, anchor bookmark or -1)`` in outline order.

    Each matched section heads a run with the unmatched sections after it; runs are
    stably sorted by their bookmark's outline position. Sections before the first
    match stay first, at their own level.
    """

    runs: list[tuple[int, list[tuple[Section, int, int]]]] = [(-1, [])]
    for index, section in enumerate(sections):
        anchor = anchors.get(index)
        if anchor is not None:
            runs.append((anchor, [(section, _clamp(bookmarks[anchor].level), anchor)]))
            continue
        head, run = runs[-1]
        depth = _clamp(run[0][1] + 1) if run and head >= 0 else _clamp(section.level)
        run.append((section, depth, -1))
    runs.sort(key=lambda item: item[0])
    return [entry for _, run in runs for entry in run]


def _bookmark_tree(bookmarks: list[PdfBookmark]) -> tuple[list[int], list[list[int]]]:
    """Each bookmark's parent index (``-1`` at the top) and direct children."""

    parents = [-1] * len(bookmarks)
    children: list[list[int]] = [[] for _ in bookmarks]
    stack: list[int] = []
    for index, bookmark in enumerate(bookmarks):
        while stack and bookmarks[stack[-1]].level >= bookmark.level:
            stack.pop()
        if stack:
            parents[index] = stack[-1]
            children[stack[-1]].append(index)
        stack.append(index)
    return parents, children


def _has_part_title(title: str) -> bool:
    return bool(_PART_TITLE.match(_normalise(title)))


def _has_part_shape(root: OutlineNode, anchor: int, outline_children: list[list[int]]) -> bool:
    """Whether ``root`` is shaped like a part (see the module docstring)."""

    if len(root.section.text.split()) > _PART_MAX_WORDS:
        return False
    if anchor >= 0:
        kids = outline_children[anchor]
        return len(kids) >= 2 and all(outline_children[kid] for kid in kids)
    return len(root.children) >= 2 and all(child.children for child in root.children)


def _clamp(level: int) -> int:
    return max(1, min(level, _MAX_DEPTH))


def _normalise(title: str) -> str:
    """Case-, punctuation- and whitespace-insensitive title key (’ and ' match)."""

    return " ".join(re.sub(r"[\W_]+", " ", title.casefold()).split())
