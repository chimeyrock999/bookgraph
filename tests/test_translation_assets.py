"""``includes_assets`` is verified against the translation body, not trusted."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.documents import write_document
from bookgraph.exports.models import ASSET_MISSING, TRANSLATION_MISSING_ASSETS
from bookgraph.exports.renderers import HtmlRenderer
from bookgraph.exports.translated import (
    ExportError,
    build_translated_export,
    write_translated_export,
)
from bookgraph.mcp import service
from bookgraph.mcp.service import ReadingServiceError
from bookgraph.models import CanonicalBlock, Document
from bookgraph.parsers.markdown import document_from_markdown
from bookgraph.sections import read_sections, write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.translation_assets import check_translation_assets
from bookgraph.translations import translation_state, write_translation
from bookgraph.workspace import WorkspacePaths
from translated_export_support import (
    DOC,
    GENERATED_AT,
    PNG,
    _blocks,
    _codes,
    _register,
    _section_ids,
    _sections,
)

FIGURE = "![Hình 1](images/fig1.png)"


def _export(workspace: WorkspacePaths):  # noqa: ANN202 - test helper
    # ``skip`` keeps the untranslated sections' original assets out of the report.
    return build_translated_export(
        workspace, DOC, lang="vi", fallback="skip", generated_at=GENERATED_AT
    )


# -- export ---------------------------------------------------------------------------


def test_misleading_sidecar_does_not_hide_a_missing_figure(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    chapter, _, _ = _section_ids(workspace)
    # The sidecar claims the figure was carried; the body is prose only.
    _register(workspace, chapter, "# Chương Một\n\nChỉ có chữ.\n", includes_assets=True)

    export = _export(workspace)

    [warning] = [w for w in export.report.warnings if w.section_id == chapter]
    assert warning.code == TRANSLATION_MISSING_ASSETS
    assert warning.reference == "images/fig1.png"
    assert warning.block_id == "b2"
    assert warning.source_path == f"sources/parsed/{DOC}/document.json"
    assert (warning.origin, warning.column) == ("translation", "mixed")
    assert f"translations/vi/{DOC}/{chapter}.md" in warning.message
    with pytest.raises(ExportError, match="strict mode"):
        write_translated_export(export, tmp_path / "out.html", HtmlRenderer(), strict=True)
    assert not (tmp_path / "out.html").exists()


def test_bilingual_missing_figure_is_a_mixed_column_translation_warning(
    workspace: WorkspacePaths,
) -> None:
    chapter, _, _ = _section_ids(workspace)
    _register(workspace, chapter, "# Chương Một\n\nChỉ có chữ.\n", includes_assets=True)

    export = build_translated_export(
        workspace, DOC, lang="vi", fallback="skip", mode="bilingual", generated_at=GENERATED_AT
    )

    # The original column embeds the figure; only the translated side lacks it.
    assert export.report.sections[0].original_assets_embedded == 1
    [warning] = [w for w in export.report.warnings if w.section_id == chapter]
    assert (warning.code, warning.column, warning.origin) == (
        TRANSLATION_MISSING_ASSETS,
        "mixed",
        "translation",
    )


def test_prose_only_claim_warning_is_tagged_translation(workspace: WorkspacePaths) -> None:
    _, second, _ = _section_ids(workspace)  # its table file was never staged
    _register(workspace, second, "# Phần Hai\n", includes_assets=False)

    [warning] = [w for w in _export(workspace).report.warnings if w.section_id == second]

    assert (warning.code, warning.reference) == (TRANSLATION_MISSING_ASSETS, None)
    assert (warning.origin, warning.column) == ("translation", "mixed")


@pytest.mark.parametrize(
    "link",
    [
        "images/fig1.png",  # AssetRef.link
        f"sources/parsed/{DOC}/images/fig1.png",  # workspace-relative
        "./images/fig1.png?v=1#top",
    ],
)
def test_translation_linking_the_figure_passes_strict(
    workspace: WorkspacePaths, tmp_path: Path, link: str
) -> None:
    chapter, _, third = _section_ids(workspace)
    _register(workspace, chapter, f"# Chương Một\n\n![Hình 1]({link})\n", includes_assets=True)
    _register(workspace, third, "# Phần Ba\n")

    export = _export(workspace)

    assert _codes(export.report, chapter) == []
    write_translated_export(export, tmp_path / "out.html", HtmlRenderer(), strict=True)


def test_html_img_counts_as_carrying_the_figure(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _register(workspace, chapter, '# Chương Một\n\n<img src="images/fig1.png" alt="Hình 1">\n')

    assert _codes(_export(workspace).report, chapter) == []


def test_untracked_prose_only_body_is_flagged(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    path = workspace.translations_root / "vi" / DOC / f"{chapter}.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Chương Một\n\nChỉ có chữ.\n", encoding="utf-8")

    codes = _codes(_export(workspace).report, chapter)

    assert TRANSLATION_MISSING_ASSETS in codes


def test_prose_only_section_needs_no_assets(workspace: WorkspacePaths, tmp_path: Path) -> None:
    _, _, third = _section_ids(workspace)  # an equation, no figure/table
    _register(workspace, third, "# Phần Ba\n\nChỉ có chữ.\n", includes_assets=False)

    export = _export(workspace)

    assert _codes(export.report, third) == []
    write_translated_export(export, tmp_path / "out.html", HtmlRenderer(), strict=True)


def test_reparse_that_stages_a_figure_makes_a_prose_only_translation_incomplete(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = _section_ids(workspace)  # its table file was never staged
    _register(workspace, second, "# Phần Hai\n\nChỉ có chữ.\n", includes_assets=True)
    # Nothing to link yet: the writer's claim stands.
    assert _codes(_export(workspace).report, second) == []

    # A re-parse stages the table; the section text (and so its hash) is unchanged.
    (workspace.sources_parsed / DOC / "images" / "missing-table.png").write_bytes(PNG)
    export = _export(workspace)

    assert export.report.sections[1].freshness == "fresh"
    [warning] = [w for w in export.report.warnings if w.section_id == second]
    assert (warning.code, warning.block_id) == (TRANSLATION_MISSING_ASSETS, "b6")
    assert warning.reference == "images/missing-table.png"


def test_markdown_ingest_end_to_end(tmp_path: Path) -> None:
    """Through the real parser and segmenter: an image the source links and staged."""

    paths = WorkspacePaths(tmp_path / "ws")
    document = document_from_markdown(
        "# Book\n\n## Figures\n\nProse.\n\n![Figure 1. A cat.](images/cat.png)\n\nMore.\n",
        doc_id="book",
        fallback_title="Book",
        source_path=str(tmp_path / "book.md"),
        parser_name="markdown",
    )
    parsed_dir = paths.sources_parsed / "book"
    write_document(document, parsed_dir)
    (parsed_dir / "images").mkdir()
    (parsed_dir / "images" / "cat.png").write_bytes(PNG)
    write_sections(
        HeadingSegmenter(target_level=2).segment(document), paths.sources_sections / "book"
    )
    section = next(
        s
        for s in read_sections(paths.sources_sections / "book" / "sections.jsonl")
        if s.title == "Figures"
    )
    (asset,) = service.get_section(paths, "book", section.id).assets
    write_translation(paths, section, "vi", "# Hình\n\nVăn xuôi.\n", includes_assets=True)

    prose = build_translated_export(paths, "book", lang="vi", generated_at=GENERATED_AT)

    assert [w.reference for w in prose.report.warnings if w.code == TRANSLATION_MISSING_ASSETS] == [
        asset.link
    ]
    service.write_section_translation(
        paths,
        "book",
        section.id,
        "vi",
        f"# Hình\n\n![Hình 1]({asset.link})\n",
        includes_assets=True,
    )
    fixed = build_translated_export(paths, "book", lang="vi", generated_at=GENERATED_AT)
    assert [w.code for w in fixed.report.warnings if w.section_id == section.id] == [
        "asset_captions_only"  # an ingest quality note, passed through
    ]


# -- registry (MCP) ------------------------------------------------------------------


def test_write_refuses_a_false_includes_assets_claim(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)

    with pytest.raises(ReadingServiceError, match=r"image b2 \(images/fig1.png\)"):
        service.write_section_translation(
            workspace, DOC, chapter, "vi", "# Chương Một\n\nChỉ có chữ.\n", includes_assets=True
        )
    assert translation_state(workspace, "vi", DOC, chapter, None).status == "missing"


def test_write_reports_includes_assets_verified_from_the_body(workspace: WorkspacePaths) -> None:
    chapter, _, third = _section_ids(workspace)

    linked = service.write_section_translation(
        workspace, DOC, chapter, "vi", f"# Chương Một\n\n{FIGURE}\n", includes_assets=False
    )
    prose = service.write_section_translation(workspace, DOC, third, "vi", "# Phần Ba\n")

    assert (linked.includes_assets, linked.missing_assets) == (True, [])
    # Nothing to carry: a prose-only section's translation is complete.
    assert prose.includes_assets is True
    # The sidecar keeps the writer's claim, not the derived value.
    stored = translation_state(workspace, "vi", DOC, chapter, None).artifact
    assert stored is not None and stored.includes_assets is False


def test_unclaimed_write_is_not_vouched_for_an_asset_a_reparse_adds(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    _, _, third = _section_ids(workspace)  # no figure/table yet
    service.write_section_translation(workspace, DOC, third, "vi", "# Phần Ba\n\nChữ.\n")
    # A re-parse adds an uncaptioned figure whose file was never staged: the section
    # text, and so its hash, stay the same.
    blocks = [
        *_blocks(),
        CanonicalBlock(id="b10", type="image", text="", asset_path="never.png", page_idx=2),
    ]
    document = Document(doc_id=DOC, title="Tiny Book", blocks=blocks)
    write_document(document, workspace.sources_parsed / DOC)
    write_sections(
        HeadingSegmenter(target_level=2).segment(document), workspace.sources_sections / DOC
    )

    view = service.get_section_translation(workspace, DOC, third, "vi")
    export = _export(workspace)

    assert (view.status, view.section_has_assets) == ("fresh", True)
    assert view.includes_assets is False
    assert _codes(export.report, third) == [TRANSLATION_MISSING_ASSETS]


def test_prose_only_write_is_recorded_with_its_missing_assets(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)

    view = service.write_section_translation(
        workspace, DOC, chapter, "vi", "# Chương Một\n\nChỉ có chữ.\n"
    )

    assert view.includes_assets is False
    [missing] = view.missing_assets
    assert (missing.block_id, missing.type, missing.link) == ("b2", "image", "images/fig1.png")
    assert missing.caption == "Figure 1. A diagram."


def test_view_verifies_a_misleading_sidecar(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    _register(workspace, chapter, "# Chương Một\n\nChỉ có chữ.\n", includes_assets=True)

    view = service.get_section_translation(workspace, DOC, chapter, "vi")
    (listed,) = [
        a
        for a in service.list_section_artifacts(workspace, DOC).artifacts
        if a.section_id == chapter
    ]

    assert view.status == "fresh"
    assert view.section_has_assets is True
    assert view.includes_assets is False
    assert [a.link for a in view.missing_assets] == ["images/fig1.png"]
    assert (listed.includes_assets, listed.missing_assets) == (False, view.missing_assets)


def test_untracked_view_lists_missing_assets_without_a_claim(workspace: WorkspacePaths) -> None:
    chapter, _, _ = _section_ids(workspace)
    path = workspace.translations_root / "vi" / DOC / f"{chapter}.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Chương Một\n", encoding="utf-8")

    view = service.get_section_translation(workspace, DOC, chapter, "vi")

    assert view.includes_assets is None
    assert [a.block_id for a in view.missing_assets] == ["b2"]


# -- the shared check ----------------------------------------------------------------


def test_check_ignores_remote_absolute_and_unrelated_images(workspace: WorkspacePaths) -> None:
    chapter = next(s for s in _sections(workspace) if s.id == _section_ids(workspace)[0])
    parsed_dir = workspace.sources_parsed / DOC
    (parsed_dir / "images" / "other.png").write_bytes(PNG)
    body = (
        "![a](https://example.com/images/fig1.png)\n\n"
        f"![b]({parsed_dir / 'images' / 'fig1.png'})\n\n"
        "![c](images/other.png)\n\n"
        "`![d](images/fig1.png)`\n"
    )

    check = check_translation_assets(
        chapter,
        body,
        blocks={b.id: b for b in _blocks()},
        root=workspace.root,
        parsed_dir=parsed_dir,
        body_dir=workspace.translations_root / "vi" / DOC,
    )

    assert [a.block_id for a in check.missing] == ["b2"]
    assert check.includes_assets(True) is False


def test_asset_missing_is_not_double_reported_as_missing_from_translation(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = _section_ids(workspace)  # its table file was never staged
    _register(workspace, second, "# Phần Hai\n", includes_assets=False)

    codes = _codes(_export(workspace).report, second)

    # The claim says prose-only: flagged once, without a link (there is none to write).
    assert codes == [TRANSLATION_MISSING_ASSETS]
    assert ASSET_MISSING not in codes
