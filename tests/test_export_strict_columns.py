"""``--strict`` diagnostics name the column and the content each problem comes from.

The same workspace can pass ``--strict`` in ``translated`` mode and fail it in
``bilingual`` mode, because the left column renders original assets the reading
edition never shows. Those failures must read as source-asset problems of the
original column, not as translation problems.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.cli import app
from bookgraph.exports.models import (
    ASSET_MISSING,
    TRANSLATION_MISSING_ASSETS,
    ExportWarning,
)
from bookgraph.exports.renderers import HtmlRenderer
from bookgraph.exports.translated import (
    ExportError,
    build_translated_export,
    write_translated_export,
)
from bookgraph.workspace import WorkspacePaths
from translated_export_support import DOC, GENERATED_AT, _register, _section_ids


def _strict(warnings: list[ExportWarning]) -> list[tuple[str, str | None, str, str]]:
    return [(w.code, w.section_id, w.column, w.origin) for w in warnings]


def test_bilingual_only_strict_failure_is_an_original_column_source_asset(
    workspace: WorkspacePaths, tmp_path: Path
) -> None:
    first, second, third = _section_ids(workspace)
    for section_id in (first, second, third):
        body = "# Một\n\n![Hình 1.](images/fig1.png)\n" if section_id == first else "# Dịch\n"
        _register(workspace, section_id, body)

    translated = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    bilingual = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    # Translated strict passes and writes the edition.
    write_translated_export(translated, tmp_path / "out.html", HtmlRenderer(), strict=True)
    assert (tmp_path / "out.html").is_file()
    # Bilingual strict fails on section two's lost table, shown only in the left column.
    [warning] = bilingual.report.strict_warnings
    assert (warning.code, warning.section_id, warning.column, warning.origin) == (
        ASSET_MISSING,
        second,
        "original",
        "source",
    )
    assert warning.source_path == f"sources/parsed/{DOC}/document.json"
    assert warning.block_id == "b6"
    with pytest.raises(ExportError) as raised:
        write_translated_export(bilingual, tmp_path / "bi.html", HtmlRenderer(), strict=True)
    message = str(raised.value)
    assert message.startswith(
        "1 problem(s) in strict mode (1 in the bilingual original column: source assets "
        "that --mode translated does not render): [original column] table asset"
    )
    assert not (tmp_path / "bi.html").exists()


def test_fallback_original_rows_report_their_assets_in_the_mixed_column(
    workspace: WorkspacePaths,
) -> None:
    # Section two is untranslated: both columns show its original, and translated mode
    # shows it too, so the warning is not bilingual-only.
    bilingual = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )
    translated = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    _, second, _ = _section_ids(workspace)
    expected = [(ASSET_MISSING, second, "mixed", "source")]
    assert _strict(bilingual.report.strict_warnings) == expected
    assert _strict(translated.report.strict_warnings) == expected
    assert "original column" not in bilingual.report.strict_summary()


def test_skip_fallback_rows_report_original_assets_in_the_original_column(
    workspace: WorkspacePaths,
) -> None:
    # ``skip`` leaves the original out of the mixed column; only the left column has it.
    bilingual = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", fallback="skip", generated_at=GENERATED_AT
    )

    _, second, _ = _section_ids(workspace)
    assert _strict(bilingual.report.strict_warnings) == [
        (ASSET_MISSING, second, "original", "source")
    ]


def test_translation_problems_stay_in_the_mixed_column_with_translation_origin(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = _section_ids(workspace)
    _register(workspace, second, "# Phần Hai\n\n![](images/gone.png)\n", includes_assets=False)

    bilingual = build_translated_export(
        workspace, DOC, lang="vi", mode="bilingual", generated_at=GENERATED_AT
    )

    by_code = {(w.code, w.column): w for w in bilingual.report.strict_warnings}
    assert by_code[(TRANSLATION_MISSING_ASSETS, "mixed")].origin == "translation"
    body_asset = by_code[(ASSET_MISSING, "mixed")]
    assert body_asset.origin == "translation"
    assert body_asset.source_path == f"translations/vi/{DOC}/{second}.md"
    # The original's lost table is still reported, apart, as a left-column source asset.
    assert by_code[(ASSET_MISSING, "original")].origin == "source"
    assert bilingual.report.strict_summary().startswith(
        f"{len(bilingual.report.strict_warnings)} problem(s) in strict mode (1 in the "
        "bilingual original column"
    )


def test_cli_tags_original_column_warnings_and_strict_summary(
    workspace: WorkspacePaths,
) -> None:
    _, second, _ = _section_ids(workspace)
    _register(workspace, second, "# Phần Hai\n\nĐã dịch.\n")

    def run(mode: str) -> tuple[int, str]:
        result = CliRunner().invoke(
            app,
            ["export", "translated-pdf", str(workspace.root), DOC]
            + ["--mode", mode, "--check", "--strict"],
        )
        return result.exit_code, result.output

    code, output = run("translated")
    assert code == 0, output
    code, output = run("bilingual")
    assert code == 1
    assert f"warning: asset_missing: {second}: [original column] table asset" in output
    assert (
        "error: 1 problem(s) in strict mode (1 in the bilingual original column: source "
        "assets that --mode translated does not render)"
    ) in output
