"""The book structure an export is assembled into: reading order, depth, chapters.

The sections manifest alone is not always the book's structure. MinerU marks every
title block as level 1, so a heading-segmented PDF comes out flat (every section a
top-level "part"), and manifests written before the bookmark segmenter kept outline
order on same-page ties list those siblings alphabetically. When the source PDF has an
outline (``sources/inbox/<doc_id>/book.json``), it is the canonical table of contents:
sections are matched to bookmarks by title, take their depth from the bookmark level,
and are put in outline order. Sections no bookmark names (a MinerU sub-heading, say)
travel with the matched section before them, one level below it. Without an outline,
or when no title matches, the manifest order and ``Section.level`` are kept.

A *chapter* starts a new page: every top-level node, and every child of a top-level
node that is a *part* (its subtree, in the outline or the manifest, reaches two levels
below it — "Part I" > "Chapter 1" > "Section"). Everything else flows inside its
chapter.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from bookgraph.models import Section
from bookgraph.pdf_metadata import PdfBookmark

_MAX_DEPTH = 6


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
    reach = _bookmark_reach(bookmarks)
    roots: list[OutlineNode] = []
    stack: list[OutlineNode] = []
    root_reach: dict[int, int] = {}
    for section, depth, anchor in placed:
        node = OutlineNode(section=section, depth=depth)
        while stack and stack[-1].depth >= depth:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
            root_reach[id(node)] = reach[anchor] if anchor >= 0 else 0
        stack.append(node)
    for root in roots:
        root.chapter = True
        deepest = max(root_reach[id(root)], *(node.depth for node in root.walk()))
        if deepest >= root.depth + 2:
            for child in root.children:
                child.chapter = True
    return roots


def flatten(roots: list[OutlineNode]) -> list[OutlineNode]:
    """Every node in reading order (pre-order)."""

    return [node for root in roots for node in root.walk()]


def _match_bookmarks(sections: list[Section], bookmarks: list[PdfBookmark]) -> dict[int, int]:
    """Section index → bookmark index, for sections whose title a bookmark carries.

    Repeated titles ("Conclusion" closes every chapter) go to the candidate on the
    nearest page when both pages are known, else to the next one after the previous
    match in outline order, so each repeat lands in its own chapter.
    """

    by_title: dict[str, list[int]] = {}
    for index, bookmark in enumerate(bookmarks):
        by_title.setdefault(_normalise(bookmark.title), []).append(index)
    anchors: dict[int, int] = {}
    used: set[int] = set()
    cursor = -1
    for index, section in enumerate(sections):
        candidates = [b for b in by_title.get(_normalise(section.title), []) if b not in used]
        if not candidates:
            continue
        chosen = min(
            candidates, key=lambda b: _rank(bookmarks[b], b, section.page_start, cursor)
        )
        anchors[index] = chosen
        used.add(chosen)
        cursor = chosen
    return anchors


def _rank(
    bookmark: PdfBookmark, index: int, page: int | None, cursor: int
) -> tuple[int, int, int]:
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


def _bookmark_reach(bookmarks: list[PdfBookmark]) -> list[int]:
    """For each bookmark, the deepest outline level in its subtree (itself included)."""

    reach = [bookmark.level for bookmark in bookmarks]
    stack: list[int] = []
    for index, bookmark in enumerate(bookmarks):
        while stack and bookmarks[stack[-1]].level >= bookmark.level:
            stack.pop()
        for ancestor in stack:
            reach[ancestor] = max(reach[ancestor], bookmark.level)
        stack.append(index)
    return reach


def _clamp(level: int) -> int:
    return max(1, min(level, _MAX_DEPTH))


def _normalise(title: str) -> str:
    """Case-, punctuation- and whitespace-insensitive title key (’ and ' match)."""

    return " ".join(re.sub(r"[\W_]+", " ", title.casefold()).split())
