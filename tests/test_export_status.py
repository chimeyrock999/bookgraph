"""Reader-facing export pages vs the ``--show-status`` debug view, and asset links.

The default reading edition carries book content only: status metadata and
missing-asset placeholders live in the report. ``show_status`` prints them on the
pages too. Also covers how a dropped image leaves no empty wrapper behind, and the
relative ``AssetRef.link`` a translation should use for a figure.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from bookgraph.documents import write_document
from bookgraph.exports.models import ASSET_MISSING
from bookgraph.exports.translated import build_translated_export
from bookgraph.mcp import service
from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.sections import read_sections, write_sections
from bookgraph.translations import write_translation
from bookgraph.workspace import WorkspacePaths

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
DOC = "tiny"
GENERATED_AT = "2026-10-02T00:00:00Z"
ONE, TWO, THREE = f"{DOC}.one", f"{DOC}.two", f"{DOC}.three"


def _section(section_id: str, title: str, block_ids: list[str], text: str) -> Section:
    return Section(
        id=section_id,
        doc_id=DOC,
        title=title,
        level=1,
        heading_path=[title],
        text=text,
        block_ids=block_ids,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths(tmp_path)
    parsed = paths.sources_parsed / DOC
    blocks = [
        CanonicalBlock(id="b1", type="text", text="One English."),
        CanonicalBlock(id="b2", type="image", text="Figure 1. Here.", asset_path="fig1.png"),
        CanonicalBlock(id="b3", type="text", text="Two English."),
        CanonicalBlock(id="b4", type="table", text="Table 1. Lost.", asset_path="gone.png"),
        CanonicalBlock(id="b5", type="text", text="Three English."),
    ]
    write_document(Document(doc_id=DOC, title="Tiny", blocks=blocks), parsed)
    (parsed / "images").mkdir()
    (parsed / "images" / "fig1.png").write_bytes(PNG)
    sections = [
        _section(ONE, "One", ["b1", "b2"], "One English."),
        _section(TWO, "Two", ["b3", "b4"], "Two English."),
        _section(THREE, "Three", ["b5"], "Three English."),
    ]
    write_sections(sections, paths.sources_sections / DOC)
    return paths


def _sections(paths: WorkspacePaths) -> dict[str, Section]:
    return {s.id: s for s in read_sections(paths.sources_sections / DOC / "sections.jsonl")}


def _register(paths: WorkspacePaths, section_id: str, body: str) -> None:
    write_translation(paths, _sections(paths)[section_id], "vi", body, includes_assets=True)


def _untracked(paths: WorkspacePaths, section_id: str, body: str) -> None:
    path = paths.translations_root / "vi" / DOC / f"{section_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _export(paths: WorkspacePaths, **options: object) -> str:
    return build_translated_export(
        paths,
        DOC,
        lang="vi",
        generated_at=GENERATED_AT,
        **options,  # type: ignore[arg-type]
    ).html


def test_default_pages_carry_no_status_and_debug_view_prints_it(
    workspace: WorkspacePaths,
) -> None:
    _register(workspace, ONE, "# Một\n\nTiếng Việt.\n")
    _untracked(workspace, THREE, "# Ba\n\nSửa tay.\n")

    clean = _export(workspace)
    debug_export = build_translated_export(
        workspace, DOC, lang="vi", generated_at=GENERATED_AT, show_status=True
    )
    debug = debug_export.html

    for status in (
        "Untranslated — original text",
        "sections translated",
        GENERATED_AT,
        '<span class="status">',
        "Translation status unknown",
        "Missing asset",
    ):
        assert status not in clean
    assert "<figcaption>Table 1. Lost.</figcaption>" in clean  # the caption stays

    assert debug_export.report.show_status is True
    assert "Untranslated — original text" in debug
    assert "2/3 sections translated (66.7%)" in debug
    assert GENERATED_AT in debug
    assert 'Two</a> <span class="status">(original)</span>' in debug
    assert "Translation status unknown — it may be outdated" in debug
    assert 'Ba</a> <span class="status">(not tracked)</span>' in debug
    assert "Missing asset: gone.png" in debug


def test_stale_translation_is_flagged_only_in_the_debug_view(workspace: WorkspacePaths) -> None:
    _register(workspace, ONE, "# Một\n\nBản cũ.\n")
    sections = _sections(workspace)
    sections[ONE] = sections[ONE].model_copy(update={"text": "One English, revised."})
    write_sections(list(sections.values()), workspace.sources_sections / DOC)

    assert "may be outdated" not in _export(workspace)
    debug = _export(workspace, show_status=True)
    assert '<h1>Một</h1>\n<p class="source-note">Translation may be outdated' in debug
    assert 'Một</a> <span class="status">(may be outdated)</span>' in debug


def test_skip_fallback_placeholder_is_debug_only(workspace: WorkspacePaths) -> None:
    _register(workspace, ONE, "# Một\n")

    clean = _export(workspace, fallback="skip")
    assert "Not translated yet" not in clean
    assert "<h1>Two</h1>" in clean  # the heading keeps the outline
    assert _export(workspace, fallback="skip", show_status=True).count("Not translated yet") == 2


def test_missing_block_asset_warning_names_its_source(workspace: WorkspacePaths) -> None:
    report = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT).report

    missing = [w for w in report.warnings if w.code == ASSET_MISSING]
    assert [(w.section_id, w.reference, w.source_path, w.block_id) for w in missing] == [
        (TWO, "gone.png", f"sources/parsed/{DOC}/document.json", "b4")
    ]


@pytest.mark.parametrize(
    "body",
    [
        "![a](nope.png)",
        '![a](nope.png) <img src="nope2.png">',
        "[![l](nope3.png)](http://x)",
        '<a href="http://x"><img src="nope.png"></a>',
        "*![a](nope.png)*",
        "**[![a](nope.png)](http://x)**",
        '<p><img src="nope.png"></p>',
        '<figure><img src="nope.png"></figure>',
    ],
)
def test_a_dropped_image_leaves_no_empty_wrapper(workspace: WorkspacePaths, body: str) -> None:
    _register(workspace, ONE, f"# Một\n\nTrước.\n\n{body}\n\n_Hình 1. Chú thích._\n")

    html = _export(workspace)

    section = html[html.index(f'id="{ONE}"') :]
    section = section[: section.index("</section>")]
    # No element is left wrapping nothing (whitespace aside).
    assert not re.search(r"<(p|a|em|strong|figure)\b[^>]*>\s*</\1>", section), section
    assert "Trước." in section
    assert "<em>Hình 1. Chú thích.</em>" in section  # the caption stays


def test_html_figure_keeps_its_caption_when_the_image_is_dropped(
    workspace: WorkspacePaths,
) -> None:
    _register(
        workspace,
        ONE,
        '# Một\n\n<figure><img src="nope.png"><figcaption>Hình 2.</figcaption></figure>\n',
    )

    html = _export(workspace)

    assert "<figure><figcaption>Hình 2.</figcaption></figure>" in html


def test_a_link_around_a_kept_image_stays(workspace: WorkspacePaths) -> None:
    _register(
        workspace, ONE, "# Một\n\n[![Hình](images/fig1.png)](http://x) và [ghi chú](http://y)\n"
    )

    html = _export(workspace)

    assert '<a href="http://x"><img src="data:image/png;base64,' in html
    assert '<a href="http://y">ghi chú</a>' in html


def test_asset_ref_link_is_relative_and_embeds_in_the_export(workspace: WorkspacePaths) -> None:
    (asset,) = service.get_section(workspace, DOC, ONE).assets

    assert asset.path == str(workspace.sources_parsed / DOC / "images" / "fig1.png")
    assert asset.link == "images/fig1.png"
    service.write_section_translation(
        workspace, DOC, ONE, "vi", f"# Một\n\n![Hình 1]({asset.link})\n", includes_assets=True
    )
    assert "data:image/png;base64," in _export(workspace)
