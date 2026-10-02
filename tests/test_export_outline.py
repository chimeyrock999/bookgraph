from __future__ import annotations

import pytest

from bookgraph.exports.outline import OutlineNode, build_outline, flatten
from bookgraph.models import Section
from bookgraph.pdf_metadata import PdfBookmark


def _section(title: str, *, level: int = 1, page: int | None = None) -> Section:
    slug = title.lower().replace(" ", "-").replace(".", "")
    return Section(
        id=f"book.{slug}",
        doc_id="book",
        title=title,
        level=level,
        heading_path=[title],
        page_start=page,
        text="",
    )


# An Iceberg-shaped outline: front matter with same-page subsections, then a part
# holding chapters holding sections.
BOOKMARKS = [
    PdfBookmark(title="Preface", page_index=21, level=1),
    PdfBookmark(title="Conventions Used in This Book", page_index=24, level=2),
    PdfBookmark(title="Feedback and Questions", page_index=24, level=2),
    PdfBookmark(title="Part I. Fundamentals", page_index=29, level=1),
    PdfBookmark(title="Chapter 1. Introduction", page_index=31, level=2),
    PdfBookmark(title="The Data Lake", page_index=38, level=3),
    PdfBookmark(title="Conclusion", page_index=55, level=3),
    PdfBookmark(title="Chapter 2. Architecture", page_index=57, level=2),
    PdfBookmark(title="Conclusion", page_index=80, level=3),
]


def _shape(nodes: list[OutlineNode]) -> list[tuple[str, int, bool]]:
    return [(node.section.title, node.depth, node.chapter) for node in flatten(nodes)]


def test_flat_heading_sections_take_depth_and_nesting_from_the_pdf_outline() -> None:
    # MinerU marks every title as level 1, so a heading-segmented PDF is flat.
    sections = [
        _section("Preface", page=21),
        _section("Conventions Used in This Book", page=24),
        _section("Feedback and Questions", page=24),
        _section("Part I. Fundamentals", page=29),
        _section("Chapter 1. Introduction", page=31),
        _section("The Data Lake", page=38),
        _section("Conclusion", page=55),
        _section("Chapter 2. Architecture", page=57),
        _section("Conclusion", page=80),
    ]

    outline = build_outline(sections, BOOKMARKS)

    assert _shape(outline) == [
        ("Preface", 1, True),
        ("Conventions Used in This Book", 2, False),  # flows inside the Preface
        ("Feedback and Questions", 2, False),
        ("Part I. Fundamentals", 1, True),
        ("Chapter 1. Introduction", 2, True),  # a part's children are chapters
        ("The Data Lake", 3, False),
        ("Conclusion", 3, False),
        ("Chapter 2. Architecture", 2, True),
        ("Conclusion", 3, False),
    ]
    part = outline[1]
    assert [child.section.title for child in part.children] == [
        "Chapter 1. Introduction",
        "Chapter 2. Architecture",
    ]
    # Each repeated "Conclusion" lands in its own chapter (matched by page).
    assert [c.section.page_start for c in part.children[0].children] == [38, 55]
    assert [c.section.page_start for c in part.children[1].children] == [80]


def test_sections_are_put_back_in_outline_order() -> None:
    # A manifest from the old bookmark segmenter: same-page siblings sorted by title.
    sections = [
        _section("Preface", page=21),
        _section("Feedback and Questions", level=2, page=24),
        _section("Conventions Used in This Book", level=2, page=24),
    ]

    outline = build_outline(sections, BOOKMARKS)

    assert [node.section.title for node in flatten(outline)] == [
        "Preface",
        "Conventions Used in This Book",
        "Feedback and Questions",
    ]


def test_a_part_is_detected_from_the_outline_even_when_sections_stop_at_chapters() -> None:
    # Segmented at level 2: chapters have no child sections, but the outline shows the
    # part goes three levels deep, so its chapters still open new pages.
    sections = [
        _section("Part I. Fundamentals", level=1, page=29),
        _section("Chapter 1. Introduction", level=2, page=31),
        _section("Chapter 2. Architecture", level=2, page=57),
    ]

    assert _shape(build_outline(sections, BOOKMARKS)) == [
        ("Part I. Fundamentals", 1, True),
        ("Chapter 1. Introduction", 2, True),
        ("Chapter 2. Architecture", 2, True),
    ]


def test_unmatched_sections_travel_with_the_section_before_them() -> None:
    sections = [
        _section("Front Matter"),  # before any match: stays first, at its own level
        _section("Chapter 2. Architecture", page=57),
        _section("A MinerU Sub-heading", page=58),
        _section("Chapter 1. Introduction", page=31),
    ]

    outline = build_outline(sections, BOOKMARKS)

    assert _shape(outline) == [
        ("Front Matter", 1, True),
        ("Chapter 1. Introduction", 2, True),
        ("Chapter 2. Architecture", 2, True),
        ("A MinerU Sub-heading", 3, False),
    ]


def test_titles_match_regardless_of_case_punctuation_and_quotes() -> None:
    bookmarks = [PdfBookmark(title="O’Reilly Online Learning", page_index=3, level=1)]
    sections = [_section("o'reilly  online learning", level=4)]

    assert _shape(build_outline(sections, bookmarks)) == [("o'reilly  online learning", 1, True)]


def test_without_an_outline_the_manifest_order_and_levels_are_kept() -> None:
    sections = [
        _section("Part I", level=1),
        _section("Chapter", level=2),
        _section("Section", level=3),
        _section("Next Chapter", level=2),
        _section("Appendix", level=1),
    ]

    assert _shape(build_outline(sections, [])) == [
        ("Part I", 1, True),
        ("Chapter", 2, True),
        ("Section", 3, False),
        ("Next Chapter", 2, True),
        ("Appendix", 1, True),
    ]


def test_an_outline_that_matches_no_section_is_ignored() -> None:
    sections = [_section("Pages 1-10", level=1), _section("Pages 11-20", level=1)]

    assert _shape(build_outline(sections, BOOKMARKS)) == [
        ("Pages 1-10", 1, True),
        ("Pages 11-20", 1, True),
    ]


def test_a_chapter_with_sub_subsections_is_not_a_part() -> None:
    # A book without parts, segmented at level 2, whose outline goes three levels deep
    # in one chapter only: page breaks must not depend on that.
    bookmarks = [
        PdfBookmark(title="Chapter 1", page_index=1, level=1),
        PdfBookmark(title="Section 1.1", page_index=2, level=2),
        PdfBookmark(title="Section 1.1.1", page_index=3, level=3),
        PdfBookmark(title="Section 1.2", page_index=4, level=2),
        PdfBookmark(title="Chapter 2", page_index=5, level=1),
        PdfBookmark(title="Section 2.1", page_index=6, level=2),
    ]
    sections = [
        _section("Chapter 1", page=1),
        _section("Section 1.1", page=2),
        _section("Section 1.2", page=4),
        _section("Chapter 2", page=5),
        _section("Section 2.1", page=6),
    ]

    assert _shape(build_outline(sections, bookmarks)) == [
        ("Chapter 1", 1, True),
        ("Section 1.1", 2, False),
        ("Section 1.2", 2, False),
        ("Chapter 2", 1, True),
        ("Section 2.1", 2, False),
    ]


def test_a_part_is_recognised_by_its_title_even_with_uneven_chapters() -> None:
    bookmarks = [
        PdfBookmark(title="Part II. Practice", page_index=1, level=1),
        PdfBookmark(title="Chapter 3", page_index=2, level=2),
        PdfBookmark(title="Setup", page_index=3, level=3),
        PdfBookmark(title="Chapter 4", page_index=5, level=2),  # no sections of its own
    ]
    sections = [
        _section("Part II. Practice", page=1),
        _section("Chapter 3", page=2),
        _section("Chapter 4", page=5),
    ]

    assert _shape(build_outline(sections, bookmarks)) == [
        ("Part II. Practice", 1, True),
        ("Chapter 3", 2, True),
        ("Chapter 4", 2, True),
    ]


def test_a_heading_never_takes_a_same_titled_bookmark_from_another_chapter() -> None:
    bookmarks = [
        PdfBookmark(title="Preface", page_index=0, level=1),
        PdfBookmark(title="Chapter 1", page_index=5, level=1),
        PdfBookmark(title="Overview", page_index=6, level=2),
        PdfBookmark(title="Chapter 2", page_index=10, level=1),
    ]
    sections = [
        _section("Preface", page=0),
        _section("Overview", page=1),  # a Preface sub-heading with no bookmark
        _section("Using code examples", page=2),
        _section("Chapter 1", page=5),
        _section("Overview", page=6),
        _section("Chapter 2", page=10),
    ]

    outline = build_outline(sections, bookmarks)

    assert [(n.section.title, n.section.page_start, n.depth) for n in flatten(outline)] == [
        ("Preface", 0, 1),
        ("Overview", 1, 2),
        ("Using code examples", 2, 2),
        ("Chapter 1", 5, 1),
        ("Overview", 6, 2),
        ("Chapter 2", 10, 1),
    ]


def test_sections_with_subsections_in_a_parts_less_chapter_do_not_open_pages() -> None:
    # Every section of the chapter has subsections, but the chapter has a real body.
    bookmarks = [
        PdfBookmark(title="Chapter 1", page_index=1, level=1),
        PdfBookmark(title="Section 1.1", page_index=2, level=2),
        PdfBookmark(title="Section 1.1.1", page_index=3, level=3),
        PdfBookmark(title="Section 1.2", page_index=4, level=2),
        PdfBookmark(title="Section 1.2.1", page_index=5, level=3),
    ]
    chapter = _section("Chapter 1", page=1).model_copy(update={"text": "word " * 400})
    sections = [chapter, _section("Section 1.1", page=2), _section("Section 1.2", page=4)]

    assert _shape(build_outline(sections, bookmarks)) == [
        ("Chapter 1", 1, True),
        ("Section 1.1", 2, False),
        ("Section 1.2", 2, False),
    ]


def _with_words(section: Section, words: int) -> Section:
    return section.model_copy(update={"text": "word " * words})


@pytest.mark.parametrize(
    ("first", "second"), [("Chapter 1", "Chapter 2"), ("Storage", "Replication")]
)
def test_a_short_chapter_intro_does_not_make_a_chapter_a_part(first: str, second: str) -> None:
    # No parts; the first chapter's sections all have subsections, the second's do not.
    # Page breaks must not differ between the two chapters. Titled chapters are caught
    # by their title; untitled ones by the book-wide shape vote (one of two roots).
    bookmarks = [
        PdfBookmark(title=first, page_index=1, level=1),
        PdfBookmark(title="Section 1.1", page_index=2, level=2),
        PdfBookmark(title="Section 1.1.1", page_index=3, level=3),
        PdfBookmark(title="Section 1.2", page_index=4, level=2),
        PdfBookmark(title="Section 1.2.1", page_index=5, level=3),
        PdfBookmark(title=second, page_index=6, level=1),
        PdfBookmark(title="Section 2.1", page_index=7, level=2),
        PdfBookmark(title="Section 2.1.1", page_index=8, level=3),
        PdfBookmark(title="Section 2.2", page_index=9, level=2),
    ]
    sections = [
        _with_words(_section(first, page=1), 120),
        _section("Section 1.1", page=2),
        _section("Section 1.2", page=4),
        _with_words(_section(second, page=6), 120),
        _section("Section 2.1", page=7),
        _section("Section 2.2", page=9),
    ]

    chapters = [t for t, _, chapter in _shape(build_outline(sections, bookmarks)) if chapter]

    assert chapters == [first, second]


# Two untitled parts, each holding chapters that have sections of their own.
UNTITLED_PARTS = [
    PdfBookmark(title="Fundamentals", page_index=1, level=1),
    PdfBookmark(title="Tables", page_index=2, level=2),
    PdfBookmark(title="Snapshots", page_index=3, level=3),
    PdfBookmark(title="Catalogs", page_index=4, level=2),
    PdfBookmark(title="Namespaces", page_index=5, level=3),
    PdfBookmark(title="Practice", page_index=6, level=1),
    PdfBookmark(title="Spark", page_index=7, level=2),
    PdfBookmark(title="Writes", page_index=8, level=3),
    PdfBookmark(title="Flink", page_index=9, level=2),
    PdfBookmark(title="Streaming", page_index=10, level=3),
]


def test_untitled_parts_are_detected_by_their_outline_shape() -> None:
    # Segmented at level 2: the chapters' sections exist only in the outline.
    sections = [_section(b.title, page=b.page_index) for b in UNTITLED_PARTS if b.level <= 2]

    assert _shape(build_outline(sections, UNTITLED_PARTS)) == [
        ("Fundamentals", 1, True),
        ("Tables", 2, True),
        ("Catalogs", 2, True),
        ("Practice", 1, True),
        ("Spark", 2, True),
        ("Flink", 2, True),
    ]


def test_untitled_parts_are_detected_by_their_manifest_shape_without_an_outline() -> None:
    sections = [_section(b.title, level=b.level) for b in UNTITLED_PARTS]

    chapters = [title for title, _, chapter in _shape(build_outline(sections, [])) if chapter]

    assert chapters == ["Fundamentals", "Tables", "Catalogs", "Practice", "Spark", "Flink"]


def test_a_part_shaped_node_with_a_long_body_is_a_chapter() -> None:
    third_part = [("Operations", 1), ("Compaction", 2), ("Rewrites", 3)]
    third_part += [("Expiry", 2), ("Snapshots Expiry", 3)]
    sections = [_section(b.title, level=b.level) for b in UNTITLED_PARTS]
    sections += [_section(title, level=level) for title, level in third_part]
    sections[0] = _with_words(sections[0], 400)

    chapters = [title for title, _, chapter in _shape(build_outline(sections, [])) if chapter]

    # Two of three top-level nodes are parts; "Fundamentals" has a chapter's worth of
    # text, so its sections flow inside it.
    assert chapters == [
        "Fundamentals",
        "Practice",
        "Spark",
        "Flink",
        "Operations",
        "Compaction",
        "Expiry",
    ]


@pytest.mark.parametrize(("page", "matched"), [(6, True), (7, True), (8, False)])
def test_a_bookmark_matches_within_one_page_of_the_section(page: int, matched: bool) -> None:
    # The bookmark is on page 6; the manifest lists the section before Chapter 1, so
    # a match moves it into Chapter 1 (outline order) and a miss leaves it first.
    bookmarks = [
        PdfBookmark(title="Chapter 1", page_index=5, level=1),
        PdfBookmark(title="Overview", page_index=6, level=2),
    ]
    sections = [_section("Overview", page=page), _section("Chapter 1", page=5)]

    shape = [(title, depth) for title, depth, _ in _shape(build_outline(sections, bookmarks))]

    if matched:
        assert shape == [("Chapter 1", 1), ("Overview", 2)]
    else:
        assert shape == [("Overview", 1), ("Chapter 1", 1)]


def test_chapter_titled_nodes_are_never_shape_parts() -> None:
    # Every chapter's sections have subsections and every intro is short, so the shape
    # vote alone would call the book "in parts"; the chapter titles say otherwise.
    bookmarks = []
    sections = []
    for number in (1, 2):
        base = number * 10
        bookmarks += [
            PdfBookmark(title=f"Chapter {number}", page_index=base, level=1),
            PdfBookmark(title=f"Section {number}.1", page_index=base + 1, level=2),
            PdfBookmark(title=f"Section {number}.1.1", page_index=base + 2, level=3),
            PdfBookmark(title=f"Section {number}.2", page_index=base + 3, level=2),
            PdfBookmark(title=f"Section {number}.2.1", page_index=base + 4, level=3),
        ]
        sections += [
            _section(f"Chapter {number}", page=base),
            _section(f"Section {number}.1", page=base + 1),
            _section(f"Section {number}.2", page=base + 3),
        ]

    chapters = [t for t, _, chapter in _shape(build_outline(sections, bookmarks)) if chapter]

    assert chapters == ["Chapter 1", "Chapter 2"]
