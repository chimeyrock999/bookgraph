"""``--mode bilingual`` with block-aligned translations: one row per aligned unit."""

from __future__ import annotations

import re
from pathlib import Path

from bookgraph.documents import write_document
from bookgraph.exports.translated import build_translated_export
from bookgraph.mcp import service
from bookgraph.models import TranslationUnit
from bookgraph.parsers.markdown import MarkdownParser
from bookgraph.sections import write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.workspace import WorkspacePaths
from translated_export_support import DOC, GENERATED_AT, PNG_URI, _sections

CHAPTER = "tiny.chapter-one"

_ROW_RE = re.compile(
    r'<table class="bilingual"><tr>'
    r'<td class="column column-original" data-column="original" lang="und">(.*?)</td>'
    r'<td class="column column-mixed" data-column="mixed" lang="vi">(.*?)</td>'
    r"</tr></table>",
    re.S,
)


def _unit(*block_ids: str, content: str) -> TranslationUnit:
    return TranslationUnit(source_block_ids=list(block_ids), content=content)


def _write_units(workspace: WorkspacePaths, *units: TranslationUnit) -> None:
    service.write_section_translation(workspace, DOC, CHAPTER, "vi", units=list(units))


def _bilingual(workspace: WorkspacePaths):
    return build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )


def _rows(html: str, section_id: str) -> list[tuple[str, str]]:
    """The section's own rows: from its ``<section>`` to its first child or its close."""

    pattern = rf'<section [^>]*id="{re.escape(section_id)}"[^>]*>(.*?)(?=<section |</section>)'
    match = re.search(pattern, html, re.S)
    assert match is not None, section_id
    rows = _ROW_RE.findall(match.group(1))
    assert "".join(f"{a}{b}" for a, b in rows), section_id
    return rows


def _entry(export, section_id: str):
    return next(e for e in export.report.sections if e.section_id == section_id)


_UNITS = (
    _unit("b0", "b1", content="# Chương Một\n\nPhần mở đầu, xem [phần hai](#tiny.section-two)."),
    _unit("b2", content="![Hình 1. Sơ đồ.](images/fig1.png)"),
    _unit("b3", content="Văn bản sau hình."),
)


def test_an_aligned_section_takes_one_row_per_unit(workspace: WorkspacePaths) -> None:
    _write_units(workspace, *_UNITS)

    export = _bilingual(workspace)
    rows = _rows(export.html, CHAPTER)

    assert len(rows) == 4  # the headings, then one row per unit
    (head_left, head_right), (intro_left, intro_right), (fig_left, fig_right), (end_l, end_r) = rows
    assert head_left == "<h1>Chapter One</h1>"
    assert head_right.startswith("<h1>Chương Một</h1>")
    assert "Intro prose in English." in intro_left and "Phần mở đầu" in intro_right
    assert '<a href="#tiny.section-two">phần hai</a>' in intro_right  # links still resolve
    assert PNG_URI in fig_left and "<figcaption>Figure 1. A diagram.</figcaption>" in fig_left
    assert f'<img src="{PNG_URI}" alt="Hình 1. Sơ đồ." />' in fig_right
    assert "Prose after the figure." in end_l and "Văn bản sau hình." in end_r
    assert "<!--bg:" not in export.html
    entry = _entry(export, CHAPTER)
    assert (entry.alignment, entry.bilingual_rows) == ("aligned", 4)
    # Untranslated sections keep their one section-level row.
    second = _entry(export, "tiny.section-two")
    assert (second.alignment, second.bilingual_rows) == (None, 1)


def test_split_units_share_a_row_and_untranslated_blocks_fill_their_own(
    workspace: WorkspacePaths,
) -> None:
    _write_units(
        workspace,
        _unit("b0", content="# Chương Một"),
        _unit("b1", content="Nửa đầu."),
        _unit("b1", content="Nửa sau."),
        _unit("b3", content="Văn bản sau hình."),
    )

    rows = _rows(_bilingual(workspace).html, CHAPTER)

    # The heading-only unit is absorbed into the heading row.
    assert len(rows) == 4
    _, (intro_left, intro_right), (fig_left, fig_right), (end_left, end_right) = rows
    assert "Intro prose in English." in intro_left
    assert "Nửa đầu." in intro_right and "Nửa sau." in intro_right
    assert PNG_URI in fig_left and fig_right == ""  # passed through untranslated
    assert "Prose after the figure." in end_left and "Văn bản sau hình." in end_right


def test_an_unaligned_translation_keeps_the_section_row(workspace: WorkspacePaths) -> None:
    body = "# Chương Một\n\nPhần mở đầu.\n\n![Hình 1.](images/fig1.png)\n\nVăn bản sau hình.\n"
    service.write_section_translation(workspace, DOC, CHAPTER, "vi", body)

    export = _bilingual(workspace)

    assert len(_rows(export.html, CHAPTER)) == 1
    entry = _entry(export, CHAPTER)
    assert (entry.alignment, entry.bilingual_rows) == ("unaligned", 1)


def test_an_invalid_alignment_falls_back_to_the_section_row(workspace: WorkspacePaths) -> None:
    _write_units(workspace, *_UNITS)
    sections = [
        s.model_copy(update={"block_ids": ["b1", "b2", "b3"]}) if s.id == CHAPTER else s
        for s in _sections(workspace)
    ]
    write_sections(sections, workspace.sources_sections / DOC)

    export = _bilingual(workspace)

    assert len(_rows(export.html, CHAPTER)) == 1
    assert _entry(export, CHAPTER).alignment == "invalid"


def test_translated_mode_renders_an_aligned_body_like_an_unaligned_one(
    workspace: WorkspacePaths,
) -> None:
    _write_units(workspace, *_UNITS)
    aligned = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    body = service.get_section_translation(workspace, DOC, CHAPTER, "vi").content
    assert body is not None
    service.write_section_translation(workspace, DOC, CHAPTER, "vi", body)

    unaligned = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert aligned.html == unaligned.html
    assert "<!--bg:" not in aligned.html
    assert _entry(aligned, CHAPTER).alignment is None


def test_markdown_book_through_parse_segment_translate_export(tmp_path: Path) -> None:
    source = tmp_path / "book.md"
    source.write_text(
        "# Habits\n\nFirst paragraph.\n\nSecond paragraph.\n\n- one\n- two\n",
        encoding="utf-8",
    )
    workspace = WorkspacePaths(tmp_path / "ws")
    document = MarkdownParser().parse(source, workspace.sources_parsed / "book")
    document = document.model_copy(update={"doc_id": "book"})
    write_document(document, workspace.sources_parsed / "book")
    sections = HeadingSegmenter(target_level=1).segment(document)
    write_sections(sections, workspace.sources_sections / "book")
    section = service.get_section(workspace, "book", sections[0].id, include_blocks=True)
    assert section.blocks is not None
    title, first, second, items = (block.id for block in section.blocks)

    written = service.write_section_translation(
        workspace,
        "book",
        section.id,
        "vi",
        units=[
            _unit(title, first, content="# Thói quen\n\nĐoạn một."),
            _unit(second, content="Đoạn hai."),
            _unit(items, content="- một\n- hai"),
        ],
    )
    export = build_translated_export(
        workspace, "book", lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    assert (written.alignment_status, written.alignment_issues) == ("aligned", [])
    rows = _rows(export.html, section.id)
    assert len(rows) == 4
    assert "First paragraph." in rows[1][0] and "Đoạn một." in rows[1][1]
    assert "Second paragraph." in rows[2][0] and "Đoạn hai." in rows[2][1]
    assert "<li>one</li>" in rows[3][0] and "<li>một</li>" in rows[3][1]


def test_a_sections_only_original_keeps_the_section_row(workspace: WorkspacePaths) -> None:
    _write_units(workspace, *_UNITS)
    # Without document.json the original is rebuilt from Section.text: no source blocks
    # to set beside the units.
    (workspace.sources_parsed / DOC / "document.json").unlink()

    export = _bilingual(workspace)

    ((left, right),) = _rows(export.html, CHAPTER)
    assert "Intro prose in English." in left and "Phần mở đầu" in right
    entry = _entry(export, CHAPTER)
    assert (entry.alignment, entry.bilingual_rows) == ("aligned", 1)


def test_a_unit_without_a_block_of_its_own_joins_the_previous_row(
    workspace: WorkspacePaths,
) -> None:
    written = service.write_section_translation(
        workspace,
        DOC,
        CHAPTER,
        "vi",
        units=[
            _unit("b0", "b1", content="# Chương Một\n\n- Mục một"),
            _unit("b3", content="  Mục một tiếp theo"),  # a list-item continuation
        ],
    )

    rows = _rows(_bilingual(workspace).html, CHAPTER)

    assert [(i.code, i.unit) for i in written.alignment_issues] == [("unit_not_a_block", 1)]
    assert len(rows) == 2
    left, right = rows[1]
    # b3's source sits beside its translation, in the row of the unit it rendered in.
    assert "Intro prose in English." in left and "Prose after the figure." in left
    assert "Mục một tiếp theo" in right
