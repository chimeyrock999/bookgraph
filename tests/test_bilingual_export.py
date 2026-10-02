"""``bookgraph export translated-pdf --mode bilingual``: original | mixed rows."""

from __future__ import annotations

import json
import re

import pytest

from bookgraph.documents import write_document
from bookgraph.exports.models import ASSET_MISSING, FallbackPolicy
from bookgraph.exports.renderers import RenderError, default_renderer_registry
from bookgraph.exports.translated import (
    UntranslatedSectionsError,
    build_translated_export,
    write_translated_export,
)
from bookgraph.models import Document
from bookgraph.sections import write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.workspace import WorkspacePaths
from translated_export_support import (
    DOC,
    GENERATED_AT,
    PNG_URI,
    _blocks,
    _codes,
    _register,
    _section_ids,
)


def _rewrite_blocks(paths: WorkspacePaths, texts: dict[str, str]) -> None:
    """Replace some original blocks' text and re-segment the document."""

    blocks = [b.model_copy(update={"text": texts.get(b.id, b.text)}) for b in _blocks()]
    document = Document(doc_id=DOC, title="Tiny Book", blocks=blocks)
    write_document(document, paths.sources_parsed / DOC)
    write_sections(HeadingSegmenter(target_level=2).segment(document), paths.sources_sections / DOC)


def _section_body(html: str, section_id: str) -> str:
    """A section's own HTML: up to its first nested child ``<section>`` or its close."""

    pattern = rf'<section [^>]*id="{re.escape(section_id)}"[^>]*>(.*?)(?=<section |</section>)'
    match = re.search(pattern, html, re.S)
    assert match is not None, section_id
    return match.group(1)


def _columns(html: str, section_id: str) -> tuple[str, str]:
    # A row ends with ``</td></tr></table>``; tables inside a cell end with newlines
    # between their closing tags, so the non-greedy cells stop at the row's own end.
    match = re.fullmatch(
        r'<table class="bilingual"><tr>'
        r'<td class="column column-original" data-column="original" lang="und">(.*?)</td>'
        r'<td class="column column-mixed" data-column="mixed" lang="vi">(.*?)</td>'
        r"</tr></table>",
        _section_body(html, section_id),
        re.S,
    )
    assert match is not None, section_id
    return match.group(1), match.group(2)


_CHAPTER_VI = (
    "# Chương Một\n\nPhần mở đầu, xem [phần hai](#tiny.section-two) và `src/app/main.py`.\n\n"
    "![Hình 1. Sơ đồ.](images/fig1.png)\n\n```python\nprint('hello')\n```\n"
)


def test_translated_mode_is_the_default_and_counts_fallbacks(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _register(workspace, chapter, _CHAPTER_VI)

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    report = export.report

    assert report.mode == "translated"
    assert (report.translated_sections, report.original_sections) == (1, 2)
    assert (report.skipped_sections, report.unpaired_sections) == (0, 0)
    assert report.assets_missing == 1  # the lost table of section two
    assert all(entry.original_assets_embedded is None for entry in report.sections)
    assert 'class="bilingual"' not in export.html
    assert "landscape" not in export.html


def test_bilingual_pairs_each_original_section_with_the_mixed_rendering(
    workspace: WorkspacePaths,
) -> None:
    chapter, second, third = _section_ids(workspace)
    _register(workspace, chapter, _CHAPTER_VI)

    mixed = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    export = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )
    html = export.html

    # Stable section order and anchors, one two-column row per section.
    body = html[html.index("<main>") :]
    positions = [body.index(f'id="{section_id}"') for section_id in (chapter, second, third)]
    assert positions == sorted(positions)
    assert html.count('<table class="bilingual">') == 3
    # The right column is exactly what translated mode renders for each section:
    # the translation where there is one, the original fallback where there is not.
    for section_id in (chapter, second, third):
        _, right = _columns(html, section_id)
        assert right == _section_body(mixed.html, section_id)

    left, right = _columns(html, chapter)
    assert left.startswith("<h1>Chapter One</h1>")
    assert left.index("Intro prose in English.") < left.index(PNG_URI)
    assert "<figcaption>Figure 1. A diagram.</figcaption>" in left
    assert "Phần mở đầu" in right and "Intro prose" not in right
    assert f'<img src="{PNG_URI}" alt="Hình 1. Sơ đồ." />' in right  # translated caption
    assert "<pre><code class=\"language-python\">print('hello')" in right
    assert "print('hello')" not in left  # code stays in the column it came from
    # Link targets, anchors and file paths are carried through untouched.
    assert '<a href="#tiny.section-two">phần hai</a>' in right
    assert "<code>src/app/main.py</code>" in right

    left, right = _columns(html, third)
    assert '<div class="equation">E = mc^2</div>' in left
    assert right == left  # a fallback row shows the same original twice, no status label

    report = export.report
    assert report.mode == "bilingual"
    assert [e.source for e in report.sections] == ["translated", "original", "original"]
    assert (report.translated_sections, report.original_sections) == (1, 2)
    assert report.unpaired_sections == 2
    chapter_entry, second_entry, _ = report.sections
    assert (chapter_entry.assets_embedded, chapter_entry.original_assets_embedded) == (1, 1)
    assert (second_entry.assets_missing, second_entry.original_assets_missing) == (1, 1)
    assert report.assets_missing == 2  # one placeholder in each column of section two
    # An original shown in both columns is still reported once.
    missing = [w for w in report.warnings if w.code == ASSET_MISSING]
    assert [(w.section_id, w.reference) for w in missing] == [(second, "missing-table.png")]
    assert report.warnings == mixed.report.warnings
    # Diagnostics stay in the report, never on the page.
    for code in (ASSET_MISSING, "unpaired", "translation_"):
        assert code not in html
    assert "@page { size: A4 landscape" in html
    assert "<dt>mode</dt>" not in html  # status metadata, shown only with show_status


def test_bilingual_original_column_reports_assets_of_translated_sections(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = _section_ids(workspace)
    _register(workspace, second, "# Phần Hai\n\nĐã dịch.\n", includes_assets=False)

    translated = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    bilingual = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    # Translated mode never shows section two's original, so its lost table is not missing
    # there; the bilingual left column shows it, so the placeholder is reported.
    assert ASSET_MISSING not in _codes(translated.report, second)
    assert ASSET_MISSING in _codes(bilingual.report, second)
    left, right = _columns(bilingual.html, second)
    assert "Table 1. Lost table." in left and "Missing asset" not in left  # caption stays
    assert "Đã dịch." in right


def test_bilingual_skip_fallback_keeps_the_original_beside_the_placeholder(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = _section_ids(workspace)

    export = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", fallback="skip", generated_at=GENERATED_AT
    )

    left, right = _columns(export.html, second)
    assert "Second section English text." in left
    assert right == "<h2>Section Two</h2>"  # title only; the placeholder needs show_status
    assert export.report.skipped_sections == 3
    assert export.report.unpaired_sections == 3


def test_bilingual_fail_fallback_lists_untranslated_sections(workspace: WorkspacePaths) -> None:
    with pytest.raises(UntranslatedSectionsError) as excinfo:
        build_translated_export(workspace, DOC, lang="vi", mode="bilingual", fallback="fail")

    assert excinfo.value.report.mode == "bilingual"
    assert len(excinfo.value.report.untranslated) == 3


def test_bilingual_export_is_deterministic(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _register(workspace, chapter, _CHAPTER_VI)

    first = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )
    second = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    assert first.html == second.html
    assert first.report == second.report


@pytest.mark.parametrize("backend", ["weasyprint", "playwright"])
def test_real_pdf_backend_renders_bilingual(workspace: WorkspacePaths, backend: str) -> None:
    renderer = default_renderer_registry().get(backend)
    if not renderer.available():
        pytest.skip(f"{backend} not installed")
    chapter, _, _ = _section_ids(workspace)
    _register(workspace, chapter, _CHAPTER_VI + "\n" + "Đoạn văn dài. " * 800 + "\n")
    export = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )
    output = workspace.exports_root / "tiny.vi-bilingual.pdf"

    try:
        write_translated_export(export, output, renderer)
    except RenderError as exc:
        pytest.skip(str(exc))

    assert output.read_bytes().startswith(b"%PDF")


def test_bilingual_html_ids_are_unique_and_anchors_land_in_the_mixed_column(
    workspace: WorkspacePaths,
) -> None:
    _rewrite_blocks(
        workspace,
        {
            "b1": 'Footnote <a id="fn1"></a> [see](#fn1)',
            "b5": 'Second section <a name="note2"></a> text.',
        },
    )
    chapter, second, _ = _section_ids(workspace)
    _register(workspace, chapter, '# Chương Một\n\nChú thích <a id="fn1"></a> [xem](#fn1)\n')

    mixed = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    export = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    anchors = re.findall(r'\s(?:id|name)="([^"]*)"', export.html)
    assert len(anchors) == len(set(anchors)), anchors
    for section_id in (chapter, second):
        left, right = _columns(export.html, section_id)
        assert right == _section_body(mixed.html, section_id)  # mixed column keeps them
        assert 'id="fn1"' not in left and 'name="note2"' not in left
    left, _ = _columns(export.html, chapter)
    assert '<a href="#fn1">see</a>' in left  # the link target itself is unchanged
    assert 'id="fn1"' in _columns(export.html, chapter)[1]
    assert 'name="note2"' in _columns(export.html, second)[1]


def test_bilingual_columns_are_styled_for_pdf_outline_images_and_language(
    workspace: WorkspacePaths,
) -> None:
    export = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )
    html = export.html

    # Only the mixed column's headings feed the PDF outline (WeasyPrint bookmarks).
    assert "td.column-original :is(h1, h2, h3, h4, h5, h6) { bookmark-level: none; }" in html
    # Raw HTML images (outside figure/p) stay inside their column too.
    assert "td.column img { max-width: 100%; height: auto;" in html
    # The original is not tagged as the target language.
    assert '<td class="column column-original" data-column="original" lang="und">' in html


@pytest.mark.parametrize(
    ("fallback", "wording", "note"),
    [
        ("original", "Untranslated sections repeat the original text.", "Untranslated —"),
        ("skip", "Untranslated sections are left out.", "Not translated yet"),
    ],
)
def test_bilingual_legend_names_columns_and_status_waits_for_show_status(
    workspace: WorkspacePaths, fallback: FallbackPolicy, wording: str, note: str
) -> None:
    _, second, _ = _section_ids(workspace)

    def export(show_status: bool) -> str:
        return build_translated_export(
            workspace,
            DOC,
            lang="vi",
            mode="bilingual",
            fallback=fallback,
            generated_at=GENERATED_AT,
            show_status=show_status,
        ).html

    clean, debug = export(False), export(True)

    legend = "Left column: original text. Right column: vi reading edition."
    assert legend in clean and legend in debug
    assert wording not in clean and note not in _columns(clean, second)[1]
    assert wording in debug and note in _columns(debug, second)[1]
    assert "<dt>mode</dt><dd>bilingual</dd>" in debug
    assert "<dt>mode</dt>" not in clean
    assert f"{fallback} fallback" not in debug


def test_bilingual_strict_also_covers_original_assets_of_translated_sections(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = _section_ids(workspace)
    _register(workspace, second, "# Phần Hai\n\nĐã dịch.\n")

    translated = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    bilingual = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    assert translated.report.strict_warnings == []
    assert [(w.code, w.section_id) for w in bilingual.report.strict_warnings] == [
        (ASSET_MISSING, second)
    ]


def test_bilingual_rows_follow_the_outline_depth_in_both_columns(
    workspace: WorkspacePaths,
) -> None:
    chapter, second, third = _section_ids(workspace)
    outline = workspace.sources_inbox / DOC / "book.json"
    outline.parent.mkdir(parents=True, exist_ok=True)
    bookmarks = [("Chapter One", 0, 1), ("Section Two", 1, 2), ("Section Three", 2, 3)]
    outline.write_text(
        json.dumps(
            {
                "pdf": {
                    "bookmarks": [
                        {"title": t, "page_index": p, "level": lv} for t, p, lv in bookmarks
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    _register(workspace, third, "# Phần Ba\n\nĐã dịch.\n")

    export = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    # The manifest says level 2; the outline puts Section Three under Section Two.
    assert [(e.depth, e.parent_id) for e in export.report.sections] == [
        (1, None),
        (2, chapter),
        (3, second),
    ]
    left, right = _columns(export.html, third)
    assert left.startswith("<h3>Section Three</h3>")
    assert right.startswith("<h3>Phần Ba</h3>")
    # The row is nested inside its parent's <section>, after the parent's own row.
    second_open = export.html.index(f'id="{second}"')
    third_open = export.html.index(f'id="{third}"')
    assert second_open < third_open < export.html.index("</section>", second_open)
