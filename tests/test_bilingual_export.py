"""``bookgraph export translated-pdf --mode bilingual``: original | mixed rows."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bookgraph.exports.models import ASSET_MISSING, FallbackPolicy
from bookgraph.exports.renderers import RenderError, default_renderer_registry
from bookgraph.exports.translated import (
    UntranslatedSectionsError,
    build_translated_export,
    write_translated_export,
)
from bookgraph.workspace import WorkspacePaths
from export_fixtures import (
    DOC,
    GENERATED_AT,
    PNG_URI,
    codes,
    make_workspace,
    register,
    section_ids,
    tiny_blocks,
    write_tiny_book,
)


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspacePaths:
    return make_workspace(tmp_path)


def _section_body(html: str, section_id: str) -> str:
    pattern = rf'<section [^>]*id="{re.escape(section_id)}"[^>]*>(.*?)</section>'
    match = re.search(pattern, html, re.S)
    assert match is not None, section_id
    return match.group(1)


def _columns(html: str, section_id: str) -> tuple[str, str]:
    match = re.fullmatch(
        r'<table class="bilingual"><tr>'
        r'<td class="column column-original" data-column="original" lang="und">(.*)</td>'
        r'<td class="column column-mixed" data-column="mixed" lang="vi">(.*)</td>'
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
    chapter, _, _ = section_ids(workspace)
    register(workspace, chapter, _CHAPTER_VI)

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
    chapter, second, third = section_ids(workspace)
    register(workspace, chapter, _CHAPTER_VI)

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
    assert "Untranslated — original text" in right and "Untranslated" not in left

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
    assert "<dt>mode</dt><dd>bilingual</dd>" in html


def test_bilingual_original_column_reports_assets_of_translated_sections(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = section_ids(workspace)
    register(workspace, second, "# Phần Hai\n\nĐã dịch.\n", includes_assets=False)

    translated = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    bilingual = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    # Translated mode never shows section two's original, so its lost table is not missing
    # there; the bilingual left column shows it, so the placeholder is reported.
    assert ASSET_MISSING not in codes(translated.report, second)
    assert ASSET_MISSING in codes(bilingual.report, second)
    left, right = _columns(bilingual.html, second)
    assert "Missing asset: missing-table.png" in left
    assert "Đã dịch." in right


def test_bilingual_skip_fallback_keeps_the_original_beside_the_placeholder(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = section_ids(workspace)

    export = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", fallback="skip", generated_at=GENERATED_AT
    )

    left, right = _columns(export.html, second)
    assert "Second section English text." in left
    assert "Not translated yet" in right and "Second section English text." not in right
    assert export.report.skipped_sections == 3
    assert export.report.unpaired_sections == 3


def test_bilingual_fail_fallback_lists_untranslated_sections(workspace: WorkspacePaths) -> None:
    with pytest.raises(UntranslatedSectionsError) as excinfo:
        build_translated_export(workspace, DOC, lang="vi", mode="bilingual", fallback="fail")

    assert excinfo.value.report.mode == "bilingual"
    assert len(excinfo.value.report.untranslated) == 3


def test_bilingual_export_is_deterministic(workspace: WorkspacePaths) -> None:
    chapter, _, _ = section_ids(workspace)
    register(workspace, chapter, _CHAPTER_VI)

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
    chapter, _, _ = section_ids(workspace)
    register(workspace, chapter, _CHAPTER_VI + "\n" + "Đoạn văn dài. " * 800 + "\n")
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
    texts = {
        "b1": 'Footnote <a id="fn1"></a> [see](#fn1)',
        "b5": 'Second section <a name="note2"></a> text.',
    }
    write_tiny_book(
        workspace, [b.model_copy(update={"text": texts.get(b.id, b.text)}) for b in tiny_blocks()]
    )
    chapter, second, _ = section_ids(workspace)
    register(workspace, chapter, '# Chương Một\n\nChú thích <a id="fn1"></a> [xem](#fn1)\n')

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
    ("fallback", "wording"),
    [
        ("original", "Untranslated sections repeat the original text."),
        ("skip", "Untranslated sections are left out."),
    ],
)
def test_bilingual_legend_describes_the_fallback_in_reader_terms(
    workspace: WorkspacePaths, fallback: FallbackPolicy, wording: str
) -> None:
    html = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", fallback=fallback, generated_at=GENERATED_AT
    ).html

    assert "Left column: original text. Right column: vi reading edition." in html
    assert wording in html
    assert f"{fallback} fallback" not in html


def test_bilingual_strict_also_covers_original_assets_of_translated_sections(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = section_ids(workspace)
    register(workspace, second, "# Phần Hai\n\nĐã dịch.\n")

    translated = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    bilingual = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    assert translated.report.strict_warnings == []
    assert [(w.code, w.section_id) for w in bilingual.report.strict_warnings] == [
        (ASSET_MISSING, second)
    ]
