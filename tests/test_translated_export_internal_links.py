"""Export-time resolution of a source book's internal links (``ch10.html#ch_x``).

The workspace is built through the real pipeline (Markdown parser → heading
segmenter), the way an EPUB converted by MarkItDown reaches the export: link
destinations keep the source book's file names and fragment ids, while the export
anchors every section on its BookGraph section id.
"""

from __future__ import annotations

import re
from pathlib import Path

from bookgraph.documents import write_document
from bookgraph.exports.links import InternalLinks
from bookgraph.exports.models import INTERNAL_LINK_UNRESOLVED, TRANSLATION_STRUCTURE_CHANGED
from bookgraph.exports.outline import OutlineNode
from bookgraph.exports.translated import build_translated_export
from bookgraph.models import Section
from bookgraph.parsers.markdown import MarkdownParser
from bookgraph.sections import read_sections, write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.translations import write_translation
from bookgraph.workspace import WorkspacePaths

GENERATED_AT = "2026-10-02T00:00:00Z"
DOC = "ddia"
CH1 = f"{DOC}.chapter-1-trade-offs-in-data-systems"
INTRO = f"{DOC}.introduction-to-distributed-systems"
CH10 = f"{DOC}.chapter-10-consistency-and-consensus"
LIN = f"{DOC}.linearizability"
PREFACE = f"{DOC}.preface"

SOURCE = """# Preface

Start with [Chapter 1](ch01.html).

# Chapter 1. Trade-Offs in Data Systems

See [Chapter 10](ch10.html#ch_consistency) and [the intro](#sec_introduction_distributed).

## Introduction to Distributed Systems

Read [linearizability](ch10.html#sec_consistency_linearizability) and
[a figure](ch10.html#fig_consistency_cap). External [spec](https://example.com/ch10.html#ch_consistency),
[mail](mailto:a@example.com), [a PDF](notes/ch10.pdf), and [a figure file](images/ch10_cap.png).

# Chapter 10. Consistency and Consensus

Back to the [preface](preface.html) and [nowhere](ch99.html#ch_missing).

## Linearizability

See [unknown](#sec_does_not_exist) twice: [again](#sec_does_not_exist).
"""


def _workspace(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths(tmp_path)
    source = tmp_path / f"{DOC}.md"
    source.write_text(SOURCE, encoding="utf-8")
    document = MarkdownParser().parse(source, tmp_path / "parse-out")
    write_document(document, paths.sources_parsed / DOC)
    write_sections(HeadingSegmenter(target_level=2).segment(document), paths.sources_sections / DOC)
    return paths


def _register(paths: WorkspacePaths, section_id: str, body: str) -> Path:
    sections = read_sections(paths.sources_sections / DOC / "sections.jsonl")
    section = next(s for s in sections if s.id == section_id)
    write_translation(paths, section, "vi", body)
    return paths.translations_root / "vi" / DOC / f"{section_id}.md"


def _hrefs(html: str) -> list[str]:
    main = html.split("<main>", 1)[1]
    return re.findall(r'<a href="([^"]*)"', main)


def test_fallback_original_links_resolve_to_section_anchors(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    export = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT)

    assert _hrefs(export.html) == [
        f"#{CH1}",  # ch01.html: the file's chapter
        f"#{CH10}",  # ch10.html#ch_consistency: the chapter itself
        f"#{INTRO}",  # #sec_introduction_distributed: a section of this chapter
        f"#{LIN}",  # ch10.html#sec_consistency_linearizability: a section of chapter 10
        f"#{CH10}",  # ch10.html#fig_…: no section of its own, so its chapter
        "https://example.com/ch10.html#ch_consistency",
        "mailto:a@example.com",
        "notes/ch10.pdf",
        "images/ch10_cap.png",
        f"#{PREFACE}",  # preface.html: the section titled "Preface"
        "ch99.html#ch_missing",
        "#sec_does_not_exist",
        "#sec_does_not_exist",
    ]
    # Every resolved target is a real anchor of the page.
    for href in _hrefs(export.html):
        if href.startswith(f"#{DOC}."):
            assert export.html.count(f'id="{href[1:]}"') == 1


def test_unresolved_internal_links_are_reported_once_in_reading_order(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    report = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT).report

    unresolved = [w for w in report.warnings if w.code == INTERNAL_LINK_UNRESOLVED]
    assert [(w.section_id, w.reference) for w in unresolved] == [
        (CH10, "ch99.html#ch_missing"),
        (LIN, "#sec_does_not_exist"),
    ]
    assert unresolved[0].source_path == f"sources/parsed/{DOC}/document.json"
    # A diagnostic for now: it does not make ``--strict`` refuse the export.
    assert report.strict_warnings == []


def test_translated_links_resolve_and_artifact_keeps_source_destinations(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    body = (
        "# Chương 1. Đánh đổi\n\n"
        "Xem [Chương 10](ch10.html#ch_consistency) và "
        "[phần mở đầu](#sec_introduction_distributed).\n"
    )
    artifact = _register(paths, CH1, body)

    export = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT)

    assert _hrefs(export.html)[1:3] == [f"#{CH10}", f"#{INTRO}"]
    # Only the export's HTML is rewritten: the registry artifact keeps the source's
    # destinations, and they still match the source section (no structure warning).
    assert artifact.read_text(encoding="utf-8").endswith(body)
    assert TRANSLATION_STRUCTURE_CHANGED not in [w.code for w in export.report.warnings]
    # The unresolved links left are in fallback-original rows: the source, mixed column.
    unresolved = [w for w in export.report.warnings if w.code == INTERNAL_LINK_UNRESOLVED]
    assert [(w.section_id, w.column, w.origin) for w in unresolved] == [
        (CH10, "mixed", "source"),
        (LIN, "mixed", "source"),
    ]


def test_translation_that_pre_resolves_a_link_still_fails_structure_check(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _register(
        paths,
        CH1,
        f"# Chương 1\n\nXem [Chương 10](#{CH10}) và "
        "[phần mở đầu](#sec_introduction_distributed).\n",
    )

    export = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT)

    structure = [w for w in export.report.warnings if w.code == TRANSLATION_STRUCTURE_CHANGED]
    assert [w.section_id for w in structure] == [CH1]
    assert "link 'ch10.html#ch_consistency' missing" in structure[0].message


def test_exact_anchor_in_the_page_wins_over_title_matching(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    _register(
        paths,
        INTRO,
        "## Giới thiệu\n\n"
        '<a id="sec_consistency_linearizability"></a>Đọc [tuyến tính hoá]'
        "(ch10.html#sec_consistency_linearizability) và [hình](ch10.html#fig_consistency_cap). "
        "[spec](https://example.com/ch10.html#ch_consistency), [mail](mailto:a@example.com), "
        "[a PDF](notes/ch10.pdf).\n",
    )

    export = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT)

    hrefs = _hrefs(export.html)
    # The translation carries the source's own anchor, so the link lands on it.
    assert hrefs[3] == "#sec_consistency_linearizability"


def test_bilingual_columns_link_to_the_one_canonical_anchor(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    _register(paths, CH1, "# Chương 1\n\nXem [Chương 10](ch10.html#ch_consistency).\n")

    export = build_translated_export(
        paths, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    row = export.html.split(f'id="{CH1}"', 1)[1].split("</tr></table>", 1)[0]
    original, mixed = row.split('data-column="mixed"', 1)
    assert f'href="#{CH10}"' in original
    assert f'href="#{CH10}"' in mixed
    assert export.html.count(f'id="{CH10}"') == 1
    # Unresolved links are reported once per section, not once per column.
    unresolved = [w for w in export.report.warnings if w.code == INTERNAL_LINK_UNRESOLVED]
    assert [(w.section_id, w.reference) for w in unresolved] == [
        (CH10, "ch99.html#ch_missing"),
        (LIN, "#sec_does_not_exist"),
    ]


def _node(section_id: str, title: str, depth: int = 1, *children: OutlineNode) -> OutlineNode:
    section = Section(
        id=section_id, doc_id=DOC, title=title, level=depth, heading_path=[title], text=""
    )
    return OutlineNode(section=section, depth=depth, chapter=depth == 1, children=list(children))


def test_resolver_maps_appendix_part_split_files_and_skips_ambiguous_or_commented() -> None:
    outline = [
        _node("p2", "Part II. Distributed Data", 1, _node("c5", "Chapter 5. Replication", 2)),
        _node("a1", "Appendix A. Glossary"),
        _node("x1", "Summary"),
        _node("x2", "Summary"),
    ]
    body = (
        '<p><a href="app01.html">A</a> <a href="appa.html#idm1">A</a> '
        '<a href="part02.html">P</a> <a class="x" href="ch05s02.html#ex_q&amp;a">C</a> '
        '<a href="summary.html">S</a> <!-- <a href="ch05.html">old</a> --> '
        "<a href='OEBPS/ch05.xhtml?x=1#sec_replication'>C</a></p>"
    )

    html, unresolved = InternalLinks(outline, [body]).rewrite(body, "x1")

    assert re.findall(r"""href=["']([^"']*)""", html) == [
        "#a1",
        "#a1",
        "#p2",
        "#c5",
        "summary.html",  # two sections titled "Summary" at one depth: ambiguous
        "ch05.html",  # inside an HTML comment: not a link
        "#c5",
    ]
    assert 'class="x" href="#c5"' in html
    assert unresolved == ["summary.html"]


def test_exact_title_beats_a_shallower_title_that_only_contains_the_words() -> None:
    outline = [
        _node(
            "c3",
            "Chapter 3. Storage and Retrieval",
            1,
            _node("ti", "Transactions and Indexes", 2),
            _node("ds", "Data Structures", 2, _node("ix", "Indexes", 3)),
        )
    ]
    links = InternalLinks(outline, [])

    assert links.rewrite('<a href="ch03.html#sec_indexes">x</a>', "c3") == (
        '<a href="#ix">x</a>',
        [],
    )
    # No exact title: the one containing the words still matches.
    assert links.rewrite('<a href="ch03.html#sec_transactions">x</a>', "c3") == (
        '<a href="#ti">x</a>',
        [],
    )


def test_bilingual_link_only_in_the_original_column_names_the_parsed_source(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    artifact = _register(paths, CH10, "# Chương 10\n\nQuay lại [lời nói đầu](preface.html).\n")

    export = build_translated_export(
        paths, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    row = export.html.split(f'id="{CH10}"', 1)[1].split("</tr></table>", 1)[0]
    original, mixed = row.split('data-column="mixed"', 1)
    assert f'href="#{PREFACE}"' in original and f'href="#{PREFACE}"' in mixed
    assert 'href="ch99.html#ch_missing"' in original
    unresolved = [w for w in export.report.warnings if w.code == INTERNAL_LINK_UNRESOLVED]
    parsed = f"sources/parsed/{DOC}/document.json"
    assert [(w.section_id, w.reference, w.source_path, w.column, w.origin) for w in unresolved] == [
        # Only in the translated row's original column.
        (CH10, "ch99.html#ch_missing", parsed, "original", "source"),
        # A fallback row: both columns show the original; reported once, for the mixed one.
        (LIN, "#sec_does_not_exist", parsed, "mixed", "source"),
    ]
    assert unresolved[0].describe().startswith("[original column]")
    # A link in the translation is attributed to its artifact.
    _register(paths, CH10, "# Chương 10\n\n[nowhere](ch99.html#ch_missing).\n")
    report = build_translated_export(
        paths, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    ).report
    first = next(w for w in report.warnings if w.code == INTERNAL_LINK_UNRESOLVED)
    assert (first.section_id, first.source_path, first.column, first.origin) == (
        CH10,
        artifact.relative_to(paths.root).as_posix(),
        "mixed",
        "translation",
    )


def test_flat_outline_scopes_a_file_to_its_reading_span_and_skips_chapter_slugs() -> None:
    # MarkItDown puts a chapter title and its sections at one level, so every section
    # is a root of its own; ids carry a per-chapter slug that is no title word.
    # A chapter's top sections sit at its own depth and have subsections, as in the
    # real DDIA EPUB.
    outline = [
        _node("c1", "Chapter 1. Trade-Offs in Data Systems Architecture"),
        _node("ov", "Operational Versus Analytical Systems", 1, _node("dw", "Data Warehousing", 2)),
        _node("dv", "Distributed Versus Single-Node Systems"),
        _node("c3", "Chapter 3. Data Models and Query Languages"),
        _node("rd", "Relational Versus Document Models", 1, _node("om", "Object Mapping", 2)),
        _node("nd", "Normalization, Denormalization, and Joins"),
        _node("c10", "Chapter 10. Consistency and Consensus"),
        _node("li", "Linearizability", 1, _node("wl", "What Makes a System Linearizable?", 2)),
        _node("pr", "Preface"),
        _node("ws", "Who Should Read This Book?"),
        _node("gl", "Glossary Terms", 1, _node("gt", "Distributed Terms", 2)),
    ]
    links = InternalLinks(outline, [])

    def target(href: str, section_id: str) -> str:
        html, missing = links.rewrite(f'<a href="{href}">x</a>', section_id)
        return missing[0] if missing else html.split('"')[1]

    assert target("ch10.html#sec_consistency_linearizability", "c1") == "#li"
    assert target("ch03.html#sec_datamodels_normalization", "c1") == "#nd"
    # Fragment-only: the linking section's file is the span of the chapter before it.
    assert target("#sec_introduction_distributed", "c1") == "#dv"
    assert target("#sec_introduction_distributed", "dv") == "#dv"
    # A file's span stops at the next chapter: chapter 3's section is not in ch10.html.
    assert target("ch10.html#sec_consistency_normalization", "c1") == "#c10"
    # From chapter 10, a fragment naming a chapter 3 section is found book-wide.
    assert target("#sec_datamodels_normalization", "li") == "#nd"
    assert target("#sec_nothing_here", "li") == "#sec_nothing_here"
    # A fragment-only link from a section with subsections still searches its chapter.
    assert target("#sec_introduction_distributed", "dw") == "#dv"
    # A leaf head without a division title stops at a same-depth node with sections:
    # preface.html holds its own sections, not the next untitled chapter's.
    assert target("preface.html#preface_who_read", "pr") == "#ws"
    assert target("preface.html#preface_distributed_terms", "pr") == "#pr"
