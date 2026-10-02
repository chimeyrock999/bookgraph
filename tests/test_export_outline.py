from __future__ import annotations

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
        _section("Part", level=1),
        _section("Chapter", level=2),
        _section("Section", level=3),
        _section("Next Chapter", level=2),
        _section("Appendix", level=1),
    ]

    assert _shape(build_outline(sections, [])) == [
        ("Part", 1, True),
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
