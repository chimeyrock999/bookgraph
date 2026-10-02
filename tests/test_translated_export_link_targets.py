"""Export behaviour of the translation link-target check (``translation_structure_changed``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.exports.models import INTERNAL_LINK_UNRESOLVED, TRANSLATION_STRUCTURE_CHANGED
from bookgraph.exports.renderers import HtmlRenderer
from bookgraph.exports.translated import (
    ExportError,
    build_translated_export,
    write_translated_export,
)
from bookgraph.models import Section
from bookgraph.sections import write_sections
from bookgraph.translations import write_translation
from bookgraph.workspace import WorkspacePaths

GENERATED_AT = "2026-10-02T00:00:00Z"
DOC = "tiny"


def _linked_workspace(tmp_path: Path) -> tuple[WorkspacePaths, Section]:
    paths = WorkspacePaths(tmp_path)
    section = Section(
        id=f"{DOC}.models",
        doc_id=DOC,
        title="Data Models",
        level=1,
        heading_path=["Data Models"],
        text="See [normalization](ch03.html#sec_datamodels_normalization) and "
        "[the query](#fig_graphql_query).",
    )
    write_sections([section], paths.sources_sections / DOC)
    return paths, section


def test_translation_that_rewrites_link_targets_is_flagged_and_strict_refuses(
    tmp_path: Path,
) -> None:
    paths, section = _linked_workspace(tmp_path)
    write_translation(
        paths,
        section,
        "vi",
        "# Mô hình dữ liệu\n\nXem [chuẩn hoá](ch03.html#chuan_hoa) và "
        "[truy vấn](#fig_graphql_query).",
    )

    export = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT)

    warnings = [w for w in export.report.warnings if w.code == TRANSLATION_STRUCTURE_CHANGED]
    assert [w.section_id for w in warnings] == [section.id]
    assert "link 'ch03.html#sec_datamodels_normalization' missing" in warnings[0].message
    assert "link 'ch03.html#chuan_hoa' added" in warnings[0].message
    assert export.report.strict_warnings == warnings
    output = paths.exports_root / "tiny.html"
    with pytest.raises(ExportError, match="strict mode"):
        write_translated_export(export, output, HtmlRenderer(), strict=True)


def test_translated_labels_and_heading_keep_targets_and_section_anchor(tmp_path: Path) -> None:
    paths, section = _linked_workspace(tmp_path)
    write_translation(
        paths,
        section,
        "vi",
        "# Mô hình dữ liệu\n\nXem [chuẩn hoá](ch03.html#sec_datamodels_normalization) và "
        "[truy vấn](#fig_graphql_query).",
    )

    export = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT)

    # The targets match the source, so the translation is not flagged; this one-section
    # book has no chapter 3 nor a section named by the figure's id, so both links stay
    # as written and are reported as unresolved internal links.
    assert [(w.code, w.reference) for w in export.report.warnings] == [
        (INTERNAL_LINK_UNRESOLVED, "ch03.html#sec_datamodels_normalization"),
        (INTERNAL_LINK_UNRESOLVED, "#fig_graphql_query"),
    ]
    # Navigation anchors on the section id, never on the translated heading text.
    assert f'id="{section.id}"' in export.html
    assert f'href="#{section.id}"' in export.html
    assert export.report.sections[0].title == "Mô hình dữ liệu"
    assert 'href="ch03.html#sec_datamodels_normalization"' in export.html


def test_only_non_image_src_in_raw_html_is_a_structure_change(tmp_path: Path) -> None:
    paths, section = _linked_workspace(tmp_path)
    svg = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>'
    write_translation(
        paths,
        section,
        "vi",
        "Xem [chuẩn hoá](ch03.html#sec_datamodels_normalization) và "
        "[truy vấn](#fig_graphql_query).\n\n"
        f"An <img src='{svg}'> inline SVG.\n\n"
        'Old <!-- <img src="old.png"> --> comment.\n\n'
        'Custom <img-zoom src="x"></img-zoom> inline.\n',
    )

    export = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT)

    # The ``data:`` image may be added and the comment is not structure; the custom
    # element's ``src="x"`` is a link the source section never had.
    structure = [w for w in export.report.warnings if w.code == TRANSLATION_STRUCTURE_CHANGED]
    assert [w.message.split(": ", 1)[1] for w in structure] == ["link 'x' added"]
