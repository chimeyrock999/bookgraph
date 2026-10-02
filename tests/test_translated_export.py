from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from bookgraph.documents import write_document
from bookgraph.exports.models import (
    ASSET_MISSING,
    ASSET_REMOTE,
    ASSET_UNSUPPORTED,
    TRANSLATION_EMPTY,
    TRANSLATION_MISSING_ASSETS,
    TRANSLATION_STALE,
    TRANSLATION_UNREADABLE,
    TRANSLATION_UNTRACKED,
    ExportReport,
)
from bookgraph.exports.renderers import (
    ExportRenderer,
    HtmlRenderer,
    RenderError,
    check_output_suffix,
    default_renderer_registry,
    select_renderer,
)
from bookgraph.exports.translated import (
    ExportError,
    UntranslatedSectionsError,
    build_translated_export,
    report_path_for,
    split_frontmatter,
    write_translated_export,
)
from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.plugins import PluginRegistry
from bookgraph.sections import read_sections, write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.translations import write_translation
from bookgraph.workspace import WorkspacePaths

# 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
GENERATED_AT = "2026-10-02T00:00:00Z"
DOC = "tiny"


def _blocks() -> list[CanonicalBlock]:
    return [
        CanonicalBlock(id="b0", type="title", text="Chapter One", level=1, page_idx=0),
        CanonicalBlock(id="b1", type="text", text="Intro prose in English.", page_idx=0),
        CanonicalBlock(
            id="b2",
            type="image",
            text="Figure 1. A diagram.",
            asset_path="fig1.png",
            page_idx=0,
        ),
        CanonicalBlock(id="b3", type="text", text="Prose after the figure.", page_idx=0),
        CanonicalBlock(id="b4", type="title", text="Section Two", level=2, page_idx=1),
        CanonicalBlock(id="b5", type="text", text="Second section English text.", page_idx=1),
        CanonicalBlock(
            id="b6",
            type="table",
            text="Table 1. Lost table.",
            asset_path="missing-table.png",
            page_idx=1,
        ),
        CanonicalBlock(id="b7", type="title", text="Section Three", level=2, page_idx=2),
        CanonicalBlock(id="b8", type="equation", text="E = mc^2", page_idx=2),
        CanonicalBlock(id="b9", type="text", text="Third section English text.", page_idx=2),
    ]


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths(tmp_path)
    document = Document(doc_id=DOC, title="Tiny Book", blocks=_blocks())
    parsed_dir = paths.sources_parsed / DOC
    write_document(document, parsed_dir)
    (parsed_dir / "images").mkdir()
    (parsed_dir / "images" / "fig1.png").write_bytes(PNG)
    sections = HeadingSegmenter(target_level=2).segment(document)
    write_sections(sections, paths.sources_sections / DOC)
    return paths


def _section_ids(paths: WorkspacePaths) -> list[str]:
    manifest = paths.sources_sections / DOC / "sections.jsonl"
    return [json.loads(line)["id"] for line in manifest.read_text().splitlines()]


def _translate(paths: WorkspacePaths, section_id: str, body: str) -> Path:
    """Drop a body at the registry path with no sidecar: an ``untracked`` translation."""

    path = paths.translations_root / "vi" / DOC / f"{section_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _sections(paths: WorkspacePaths) -> list[Section]:
    return read_sections(paths.sources_sections / DOC / "sections.jsonl")


def _register(
    paths: WorkspacePaths, section_id: str, body: str, *, includes_assets: bool = True
) -> None:
    """Write a translation through the registry: ``fresh`` against the current section."""

    section = next(s for s in _sections(paths) if s.id == section_id)
    write_translation(paths, section, "vi", body, includes_assets=includes_assets)


def _change_section_text(paths: WorkspacePaths, section_id: str) -> None:
    """Simulate a re-segment that changed the section's words, making it stale."""

    sections = [
        s.model_copy(update={"text": s.text + " Revised."}) if s.id == section_id else s
        for s in _sections(paths)
    ]
    write_sections(sections, paths.sources_sections / DOC)


def _codes(report: ExportReport, section_id: str) -> list[str]:
    return [w.code for w in report.warnings if w.section_id == section_id]


def test_mixed_sections_render_translation_or_original_in_reading_order(
    workspace: WorkspacePaths,
) -> None:
    chapter, second, third = _section_ids(workspace)
    _translate(
        workspace,
        chapter,
        '---\ntitle: "Chương Một"\n---\n# Chương Một\n\nPhần mở đầu tiếng Việt.\n\n'
        "![Hình 1](images/fig1.png)\n",
    )
    _register(workspace, third, "# Phần Ba\n\nVăn bản tiếng Việt.\n")

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    report = export.report

    assert [entry.source for entry in report.sections] == ["translated", "original", "translated"]
    assert report.translated_sections == 2
    assert report.total_sections == 3
    assert report.coverage == pytest.approx(0.6667)
    assert report.untranslated == [second]
    assert report.sections[0].title == "Chương Một"
    assert report.sections[2].artifact == f"translations/vi/{DOC}/{third}.md"
    assert [entry.freshness for entry in report.sections] == ["untracked", None, "fresh"]

    html = export.html
    assert "Phần mở đầu tiếng Việt." in html
    assert "Văn bản tiếng Việt." in html
    assert "Intro prose in English." not in html  # translated sections drop the original
    assert "Second section English text." in html  # untranslated falls back to English
    body = html[html.index("<main>") :]
    assert body.index("Chương Một") < body.index("Second section") < body.index("Phần Ba")
    assert PNG_URI in html
    assert "Untranslated — original text" not in html  # status stays in the report


def test_original_fallback_keeps_assets_and_equations_in_block_order(
    workspace: WorkspacePaths,
) -> None:
    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    html = export.html

    figure = html.index(PNG_URI)
    assert html.index("Intro prose in English.") < figure < html.index("Prose after the figure.")
    assert "<figcaption>Figure 1. A diagram.</figcaption>" in html
    assert '<div class="equation">E = mc^2</div>' in html
    assert html.count("<h1>Chapter One</h1>") == 1  # the section title block is not repeated

    chapter_entry, second_entry, _ = export.report.sections
    assert chapter_entry.assets_embedded == 1
    assert second_entry.assets_missing == 1
    missing = [w for w in export.report.warnings if w.code == ASSET_MISSING]
    assert [(w.section_id, w.reference) for w in missing] == [
        (second_entry.section_id, "missing-table.png")
    ]
    assert "<figcaption>Table 1. Lost table.</figcaption>" in html  # the caption stays
    assert "Missing asset" not in html


def test_translated_headings_are_relevelled_to_the_section_level(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = _section_ids(workspace)
    _translate(workspace, second, "# Phần Hai\n\n## Tiểu mục\n\nNội dung.\n")

    html = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT).html

    assert "<h2>Phần Hai</h2>" in html
    assert "<h3>Tiểu mục</h3>" in html


def test_translation_without_heading_gets_the_original_title(workspace: WorkspacePaths) -> None:
    _, second, _ = _section_ids(workspace)
    _register(workspace, second, '---\ntitle: "Phần Hai"\n---\nChỉ có nội dung.\n')

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert "<h2>Phần Hai</h2><p>Chỉ có nội dung.</p>" in export.html
    assert export.report.sections[1].title == "Phần Hai"


def test_legacy_translation_cache_is_not_read(workspace: WorkspacePaths) -> None:
    # ``translation_cache/`` is not part of the registry: the export reads translations
    # only through ``bookgraph.translations``.
    chapter, _, _ = _section_ids(workspace)
    legacy = workspace.root / "translation_cache" / DOC / f"{chapter}.vi.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# Cached\n\nBản cũ.\n", encoding="utf-8")

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert export.report.sections[0].source == "original"
    assert "Bản cũ." not in export.html


def test_broken_remote_and_escaping_image_links_are_reported_not_embedded(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    chapter, _, _ = _section_ids(workspace)
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(PNG)
    (workspace.sources_parsed / DOC / "notes.txt").write_text("not an image")
    _translate(
        workspace,
        chapter,
        "# Chương\n\n"
        "![gone](images/nope.png)\n\n"
        "![remote](https://example.com/x.png)\n\n"
        "![escape](../../../../outside.png)\n\n"
        f"![absolute]({outside})\n\n"
        "![text](notes.txt)\n\n"
        f"![ok]({workspace.sources_parsed / DOC / 'images' / 'fig1.png'})\n",
    )

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    by_ref = {w.reference: w.code for w in export.report.warnings if w.section_id == chapter}

    assert by_ref["images/nope.png"] == ASSET_MISSING
    assert by_ref["https://example.com/x.png"] == ASSET_REMOTE
    assert by_ref["../../../../outside.png"] == ASSET_MISSING
    assert by_ref[str(outside)] == ASSET_MISSING
    assert by_ref["notes.txt"] == ASSET_UNSUPPORTED
    assert export.report.sections[0].assets_embedded == 1  # the absolute in-workspace path
    assert export.report.sections[0].assets_missing == 5
    assert 'src="https://' not in export.html
    assert f'src="{outside}"' not in export.html
    assert "Missing asset" not in export.html


def test_empty_translation_falls_back_with_a_warning(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _translate(workspace, chapter, "---\ntitle: x\n---\n\n")

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert export.report.sections[0].source == "original"
    assert [w.code for w in export.report.warnings if w.section_id == chapter] == [
        TRANSLATION_EMPTY
    ]


def test_skip_fallback_renders_placeholders(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _translate(workspace, chapter, "# Chương Một\n\nTiếng Việt.\n")

    export = build_translated_export(
        workspace, DOC, lang="vi", fallback="skip", generated_at=GENERATED_AT
    )

    assert [e.source for e in export.report.sections] == ["translated", "skipped", "skipped"]
    assert "Second section English text." not in export.html
    assert "Not translated yet" not in export.html
    assert "<h2>Section Two</h2>" in export.html  # the heading keeps the outline
    # Assets of skipped sections are not rendered, so they are not reported missing.
    assert not [w for w in export.report.warnings if w.code == ASSET_MISSING]


def test_fail_fallback_lists_every_untranslated_section(workspace: WorkspacePaths) -> None:
    chapter, second, third = _section_ids(workspace)
    _translate(workspace, chapter, "# Chương\n")

    with pytest.raises(UntranslatedSectionsError) as excinfo:
        build_translated_export(workspace, DOC, lang="vi", fallback="fail")

    assert excinfo.value.report.untranslated == [second, third]
    assert second in str(excinfo.value) and third in str(excinfo.value)


def test_fully_translated_book_passes_fail_fallback(workspace: WorkspacePaths) -> None:
    for section_id in _section_ids(workspace):
        _translate(workspace, section_id, f"# {section_id}\n")

    export = build_translated_export(workspace, DOC, lang="vi", fallback="fail")

    assert export.report.coverage == 1.0


def test_export_is_deterministic(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _translate(workspace, chapter, "# Chương\n\n![](images/fig1.png)\n")

    first = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    second = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert first.html == second.html
    assert first.report == second.report


def test_source_date_epoch_pins_the_timestamp(
    workspace: WorkspacePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")

    export = build_translated_export(workspace, DOC, lang="vi")

    assert export.report.generated_at == "1970-01-01T00:00:00Z"


def test_sections_only_workspace_renders_section_text(tmp_path: Path) -> None:
    paths = WorkspacePaths(tmp_path)
    write_sections(
        [
            Section(
                id=f"{DOC}.only",
                doc_id=DOC,
                title="Only",
                level=1,
                heading_path=["Only"],
                text="Plain *markdown* text.",
            )
        ],
        paths.sources_sections / DOC,
    )

    export = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT)

    assert "<em>markdown</em>" in export.html
    assert export.report.title == DOC


def test_missing_manifest_is_an_export_error(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="Sections manifest not found"):
        build_translated_export(WorkspacePaths(tmp_path), DOC, lang="vi")


def test_frontmatter_split() -> None:
    fields, body = split_frontmatter('---\ntitle: "A: b"\nlevel: 2\nraw: plain\n---\n# H\n')
    assert fields == {"title": "A: b", "level": 2, "raw": "plain"}
    assert body == "# H"
    assert split_frontmatter("# No frontmatter\n") == ({}, "# No frontmatter\n")
    assert split_frontmatter("---\nunterminated\n") == ({}, "---\nunterminated\n")


class _FakePdf(ExportRenderer):
    name = "fake-pdf"
    suffix = ".pdf"

    def __init__(self, *, available: bool = True, fail: bool = False) -> None:
        self._available = available
        self._fail = fail
        self.rendered: list[str] = []

    def available(self) -> bool:
        return self._available

    def render(self, html: str, output: Path) -> None:
        if self._fail:
            output.write_text("partial")
            raise RenderError("boom")
        self.rendered.append(html)
        output.write_bytes(b"%PDF-fake")


def test_write_export_renders_and_writes_report(workspace: WorkspacePaths) -> None:
    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    renderer = _FakePdf()
    output = workspace.exports_root / "tiny.vi-progress.pdf"

    write_translated_export(export, output, renderer)

    assert output.read_bytes() == b"%PDF-fake"
    assert renderer.rendered == [export.html]
    stored = json.loads(report_path_for(output).read_text())
    assert report_path_for(output).name == "tiny.vi-progress.report.json"
    assert stored["renderer"] == "fake-pdf"
    assert stored["output"] == str(output)
    assert stored["total_sections"] == 3


def test_strict_mode_refuses_missing_assets_without_writing(workspace: WorkspacePaths) -> None:
    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    output = workspace.exports_root / "tiny.pdf"

    with pytest.raises(ExportError, match="strict mode"):
        write_translated_export(export, output, _FakePdf(), strict=True)

    assert not output.exists()
    assert not report_path_for(output).exists()


def test_failed_render_leaves_no_partial_output(workspace: WorkspacePaths) -> None:
    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    output = workspace.exports_root / "tiny.pdf"

    with pytest.raises(RenderError):
        write_translated_export(export, output, _FakePdf(fail=True))

    assert list(workspace.exports_root.iterdir()) == []


def test_html_renderer_writes_the_page(tmp_path: Path) -> None:
    output = tmp_path / "x.html"
    HtmlRenderer().render("<p>xin chào</p>", output)
    assert output.read_text(encoding="utf-8") == "<p>xin chào</p>"


def test_select_renderer() -> None:
    registry: PluginRegistry[ExportRenderer] = PluginRegistry(kind="export renderer")
    registry.register(HtmlRenderer())
    unavailable = _FakePdf(available=False)
    unavailable.name = "weasyprint"
    registry.register(unavailable)

    assert select_renderer(registry, "auto", Path("x.html")).name == "html"
    with pytest.raises(RenderError, match="No PDF renderer is available"):
        select_renderer(registry, "auto", Path("x.pdf"))
    with pytest.raises(RenderError, match="'weasyprint' is not available"):
        select_renderer(registry, "weasyprint", Path("x.pdf"))

    available = _FakePdf()
    available.name = "playwright"
    registry.register(available)
    assert select_renderer(registry, "auto", Path("x.pdf")) is available


def test_default_registry_names() -> None:
    assert default_renderer_registry().names() == ["html", "playwright", "weasyprint"]


@pytest.mark.parametrize("backend", ["weasyprint", "playwright"])
def test_real_pdf_backend_renders_vietnamese(workspace: WorkspacePaths, backend: str) -> None:
    renderer = default_renderer_registry().get(backend)
    if not renderer.available():
        pytest.skip(f"{backend} not installed")
    chapter, _, _ = _section_ids(workspace)
    _translate(workspace, chapter, "# Chương Một\n\nTiếng Việt có dấu.\n\n![](images/fig1.png)\n")
    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    output = workspace.exports_root / "tiny.pdf"

    try:
        write_translated_export(export, output, renderer)
    except RenderError as exc:  # e.g. playwright installed without its browser
        pytest.skip(str(exc))

    assert output.read_bytes().startswith(b"%PDF")


@pytest.mark.parametrize("body", ["", "   \n", "---\ntitle: x\n---\n"])
def test_fail_fallback_counts_empty_artifacts_as_untranslated(
    workspace: WorkspacePaths, body: str
) -> None:
    chapter, second, third = _section_ids(workspace)
    _translate(workspace, chapter, "# Chương\n")
    _translate(workspace, second, body)
    _translate(workspace, third, "# Phần Ba\n")

    with pytest.raises(UntranslatedSectionsError) as excinfo:
        build_translated_export(workspace, DOC, lang="vi", fallback="fail")

    assert excinfo.value.report.untranslated == [second]
    assert TRANSLATION_EMPTY in [w.code for w in excinfo.value.report.warnings]


def test_raw_html_images_are_embedded_or_reported(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _translate(
        workspace,
        chapter,
        "# Chương\n\n"
        '<figure><img alt="ok" src="images/fig1.png"></figure>\n\n'
        "Inline <img src='images/gone.png'> and <img data-src=\"images/fig1.png\"> here.\n\n"
        "<table><tr><td><img src=https://example.com/t.png></td></tr></table>\n",
    )

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    by_ref = {w.reference: w.code for w in export.report.warnings if w.section_id == chapter}

    assert f'<img alt="ok" src="{PNG_URI}">' in export.html
    assert by_ref["images/gone.png"] == ASSET_MISSING
    assert by_ref["https://example.com/t.png"] == ASSET_REMOTE
    assert by_ref['<img data-src="images/fig1.png">'] == ASSET_MISSING  # no real src
    assert export.report.sections[0].assets_embedded == 1
    assert export.report.sections[0].assets_missing == 3
    assert "<img src='images/gone.png'>" not in export.html
    assert export.report.asset_warnings  # so --strict refuses this export


def test_output_suffix_must_match_renderer() -> None:
    registry = default_renderer_registry()

    check_output_suffix(registry, "html", Path("book.htm"))
    check_output_suffix(registry, "weasyprint", Path("book.PDF"))
    check_output_suffix(registry, "auto", Path("book.pdf"))
    check_output_suffix(registry, "auto", Path("book.html"))
    with pytest.raises(RenderError, match="writes .html files"):
        check_output_suffix(registry, "html", Path("book.pdf"))
    with pytest.raises(RenderError, match="writes .pdf files"):
        check_output_suffix(registry, "playwright", Path("book.html"))
    with pytest.raises(RenderError, match="Cannot infer"):
        check_output_suffix(registry, "auto", Path("book.txt"))
    with pytest.raises(RenderError, match="writes .html files"):
        select_renderer(registry, "html", Path("book.pdf"))


def test_raw_html_img_scanning_is_attribute_aware(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    svg = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>'
    _register(
        workspace,
        chapter,
        "# Chương\n\n"
        "A <img alt='a src=x' src=\"images/fig1.png\"> decoy.\n\n"
        f"An <img src='{svg}'> inline SVG.\n\n"
        'Old <!-- <img src="old.png"> --> comment.\n\n'
        '<!--\n<img src="gone-block.png">\n-->\n\n'
        'Custom <img-zoom src="x"></img-zoom> inline.\n\n'
        "<img-comparison-slider>\n<p>slider</p>\n</img-comparison-slider>\n",
    )

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    html = export.html

    # The decoy ``src=x`` inside ``alt`` is ignored; the real src is embedded.
    assert f"<img alt='a src=x' src=\"{PNG_URI}\">" in html
    # A '>' inside a quoted value does not end the tag; the value is re-quoted escaped.
    assert (
        'src="data:image/svg+xml,&lt;svg xmlns=&quot;http://www.w3.org/2000/svg&quot;/&gt;"' in html
    )
    # Commented-out images are left alone and never reported.
    assert '<!-- <img src="old.png"> -->' in html
    assert '<img src="gone-block.png">' in html
    # Custom elements named ``img-*`` are not images.
    assert '<img-zoom src="x"></img-zoom>' in html
    assert "<img-comparison-slider>" in html
    assert [w for w in export.report.warnings if w.section_id == chapter] == []
    assert export.report.sections[0].assets_embedded == 2
    assert export.report.sections[0].assets_missing == 0


def test_toc_title_drops_heading_markdown(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _translate(workspace, chapter, "# *Giới thiệu* `code` [liên kết](x)\n\nNội dung.\n")

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert export.report.sections[0].title == "Giới thiệu code liên kết"
    assert '<a href="#tiny.chapter-one">Giới thiệu code liên kết</a>' in export.html


@pytest.mark.parametrize(("doc_id", "lang"), [(DOC, "../vi"), ("../x", "vi"), ("X", "vi")])
def test_api_rejects_non_slug_ids(workspace: WorkspacePaths, doc_id: str, lang: str) -> None:
    with pytest.raises(ExportError, match="must be a lowercase hyphenated slug"):
        build_translated_export(workspace, doc_id, lang=lang)


def test_lang_is_normalised_like_the_registry(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _register(workspace, chapter, "# Chương Một\n")

    export = build_translated_export(workspace, DOC, lang=" VI ", generated_at=GENERATED_AT)

    assert export.report.lang == "vi"
    assert export.report.sections[0].source == "translated"


def test_reads_translations_written_through_the_registry(workspace: WorkspacePaths) -> None:
    from bookgraph.mcp.service import write_section_translation

    chapter, _, _ = _section_ids(workspace)
    write_section_translation(workspace, DOC, chapter, "vi", "# Chương Một\n\nTừ registry.\n")

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert export.report.sections[0].source == "translated"
    assert export.report.sections[0].artifact == f"translations/vi/{DOC}/{chapter}.md"
    assert "Từ registry." in export.html
    assert '"source_section_hash"' not in export.html  # the .json sidecar is not content


def test_fresh_registry_translation_has_no_freshness_warning(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _register(workspace, chapter, "# Chương Một\n\nMới.\n")

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert export.report.sections[0].freshness == "fresh"
    assert _codes(export.report, chapter) == []
    assert "may be outdated" not in export.html


def test_stale_translation_renders_flagged_and_counts_as_translated(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    chapter, second, third = _section_ids(workspace)
    _register(workspace, chapter, "# Chương Một\n\nBản dịch cũ.\n")
    _register(workspace, second, "# Phần Hai\n")
    _register(workspace, third, "# Phần Ba\n")
    _change_section_text(workspace, chapter)

    # ``--fallback fail`` still accepts it: a stale translation is rendered.
    export = build_translated_export(
        workspace, DOC, lang="vi", fallback="fail", generated_at=GENERATED_AT
    )
    entry = export.report.sections[0]

    assert entry.source == "translated"
    assert entry.freshness == "stale"
    assert export.report.translated_sections == 3
    assert _codes(export.report, chapter) == [TRANSLATION_STALE]
    assert "Bản dịch cũ." in export.html
    assert "may be outdated" not in export.html  # flagged in the report, not on the page

    # ``--strict`` refuses to present it as current.
    with pytest.raises(ExportError, match="strict mode"):
        write_translated_export(export, tmp_path / "out.html", HtmlRenderer(), strict=True)
    assert not (tmp_path / "out.html").exists()


@pytest.mark.parametrize("edited_after_registration", [False, True])
def test_untracked_translation_renders_with_a_warning(
    workspace: WorkspacePaths, tmp_path: Path, edited_after_registration: bool
) -> None:
    chapter, _, _ = _section_ids(workspace)
    if edited_after_registration:
        _register(workspace, chapter, "# Chương Một\n\nBản gốc.\n")
    _translate(workspace, chapter, "# Chương Một\n\nSửa tay.\n")

    export = build_translated_export(
        workspace, DOC, lang="vi", fallback="skip", generated_at=GENERATED_AT
    )

    assert export.report.sections[0].source == "translated"
    assert export.report.sections[0].freshness == "untracked"
    assert _codes(export.report, chapter) == [TRANSLATION_UNTRACKED]
    assert "Sửa tay." in export.html
    assert "Translation status unknown" not in export.html
    assert "not tracked" not in export.html
    # Freshness unknown is not known-bad: ``--strict`` still writes the export.
    report = write_translated_export(export, tmp_path / "out.html", HtmlRenderer(), strict=True)
    assert report.sections[0].freshness == "untracked"


def test_prose_only_translation_of_section_with_assets_is_flagged(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    chapter, _, third = _section_ids(workspace)  # chapter has a figure, third has none
    _register(workspace, chapter, "# Chương Một\n\nChỉ có chữ.\n", includes_assets=False)
    _register(workspace, third, "# Phần Ba\n", includes_assets=False)

    export = build_translated_export(
        workspace, DOC, lang="vi", fallback="skip", generated_at=GENERATED_AT
    )

    assert export.report.sections[0].freshness == "fresh"
    assert _codes(export.report, chapter) == [TRANSLATION_MISSING_ASSETS]
    assert _codes(export.report, third) == []
    with pytest.raises(ExportError, match="strict mode"):
        write_translated_export(export, tmp_path / "out.html", HtmlRenderer(), strict=True)


def test_translation_with_assets_is_not_flagged(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _register(workspace, chapter, "# Chương Một\n\n![Hình 1](images/fig1.png)\n")

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert _codes(export.report, chapter) == []


def test_non_utf8_translation_is_unreadable_and_falls_back(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    path = _translate(workspace, chapter, "")
    path.write_bytes(b"# Ch\xff\xfe\n")

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert export.report.sections[0].source == "original"
    assert export.report.sections[0].freshness is None
    assert _codes(export.report, chapter) == [TRANSLATION_UNREADABLE]


def test_body_that_exists_but_cannot_be_read_is_unreadable(workspace: WorkspacePaths) -> None:
    chapter, second, third = _section_ids(workspace)
    _register(workspace, second, "# Phần Hai\n")
    _register(workspace, third, "# Phần Ba\n")
    # A directory at the body path: unreadable on every platform (unlike chmod as root).
    (workspace.translations_root / "vi" / DOC / f"{chapter}.md").mkdir(parents=True)

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert export.report.sections[0].source == "original"
    warning = next(w for w in export.report.warnings if w.section_id == chapter)
    assert warning.code == TRANSLATION_UNREADABLE
    assert "exists but could not be read" not in warning.message  # the OSError cause is kept
    assert "unreadable (" in warning.message
    with pytest.raises(UntranslatedSectionsError) as excinfo:
        build_translated_export(workspace, DOC, lang="vi", fallback="fail")
    assert excinfo.value.report.untranslated == [chapter]
