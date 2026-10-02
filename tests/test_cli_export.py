from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app
from bookgraph.exports.translated import report_path_for
from bookgraph.models import Section
from bookgraph.sections import write_sections
from bookgraph.workspace import WorkspacePaths

DOC = "tiny"


def _workspace(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths(tmp_path)
    sections = [
        Section(
            id=f"{DOC}.{slug}",
            doc_id=DOC,
            title=title,
            level=1,
            heading_path=[title],
            text=f"{title} English text.",
        )
        for slug, title in [("one", "One"), ("two", "Two")]
    ]
    write_sections(sections, paths.sources_sections / DOC)
    translation = paths.translations_root / "vi" / DOC / f"{DOC}.one.md"
    translation.parent.mkdir(parents=True)
    translation.write_text("# Một\n\nTiếng Việt.\n![](images/gone.png)\n", encoding="utf-8")
    return paths


def test_export_html_writes_edition_and_report(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    result = CliRunner().invoke(
        app, ["export", "translated-pdf", str(tmp_path), DOC, "--renderer", "html"]
    )

    assert result.exit_code == 0, result.output
    output = paths.exports_root / f"{DOC}.vi-progress.html"
    report = paths.exports_root / f"{DOC}.vi-progress.report.json"
    assert f"export: {output}" in result.output
    assert "coverage: 1/2 (50.0%)" in result.output
    assert "warning: asset_missing: tiny.one:" in result.output
    html = output.read_text(encoding="utf-8")
    assert "Tiếng Việt." in html and "Two English text." in html
    payload = json.loads(report.read_text())
    assert payload["renderer"] == "html"
    assert [s["source"] for s in payload["sections"]] == ["translated", "original"]


def test_export_relative_out_lands_under_workspace(tmp_path: Path) -> None:
    _workspace(tmp_path)

    result = CliRunner().invoke(
        app, ["export", "translated-pdf", str(tmp_path), DOC, "--out", "book/vi.html"]
    )

    assert result.exit_code == 0, result.output
    assert "renderer: html" in result.output  # auto picks HTML for a .html output
    assert (tmp_path / "book" / "vi.html").is_file()


def test_export_check_writes_nothing(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    result = CliRunner().invoke(app, ["export", "translated-pdf", str(tmp_path), DOC, "--check"])

    assert result.exit_code == 0, result.output
    assert "export: (check only, not written)" in result.output
    assert not paths.exports_root.exists()


def test_export_fallback_fail_exits_non_zero(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "export",
            "translated-pdf",
            str(tmp_path),
            DOC,
            "--fallback",
            "fail",
            "--renderer",
            "html",
        ],
    )

    assert result.exit_code == 1
    assert "tiny.two" in result.output
    assert not paths.exports_root.exists()


def test_export_strict_fails_on_missing_asset(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    result = CliRunner().invoke(
        app,
        ["export", "translated-pdf", str(tmp_path), DOC, "--renderer", "html", "--strict"],
    )

    assert result.exit_code == 1
    assert "strict mode" in result.output
    assert not paths.exports_root.exists()


def test_export_rejects_bad_options(tmp_path: Path) -> None:
    _workspace(tmp_path)
    runner = CliRunner()

    bad_fallback = runner.invoke(
        app, ["export", "translated-pdf", str(tmp_path), DOC, "--fallback", "maybe"]
    )
    bad_renderer = runner.invoke(
        app, ["export", "translated-pdf", str(tmp_path), DOC, "--renderer", "word"]
    )
    bad_lang = runner.invoke(
        app, ["export", "translated-pdf", str(tmp_path), DOC, "--lang", "../x"]
    )
    missing_doc = runner.invoke(app, ["export", "translated-pdf", str(tmp_path), "nope"])

    assert bad_fallback.exit_code == 2
    assert bad_renderer.exit_code == 2
    assert bad_lang.exit_code == 2
    assert missing_doc.exit_code == 2
    assert "Sections manifest not found" in missing_doc.output


def test_export_check_strict_fails_like_the_real_export(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    result = CliRunner().invoke(
        app, ["export", "translated-pdf", str(tmp_path), DOC, "--check", "--strict"]
    )

    assert result.exit_code == 1
    assert "problem(s) in strict mode" in result.output
    assert not paths.exports_root.exists()


def test_export_rejects_output_suffix_that_does_not_match_renderer(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    runner = CliRunner()

    html_as_pdf = runner.invoke(
        app,
        ["export", "translated-pdf", str(tmp_path), DOC, "--renderer", "html", "--out", "x.pdf"],
    )
    unknown = runner.invoke(
        app, ["export", "translated-pdf", str(tmp_path), DOC, "--out", "x.txt", "--check"]
    )

    assert html_as_pdf.exit_code == 2
    assert "writes .html files" in html_as_pdf.output
    assert unknown.exit_code == 2
    assert not paths.exports_root.exists()
    assert not (tmp_path / "x.pdf").exists()


def test_export_show_status_prints_status_on_the_pages(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    output = paths.exports_root / f"{DOC}.vi-progress.html"
    command = ["export", "translated-pdf", str(tmp_path), DOC, "--renderer", "html"]

    result = CliRunner().invoke(app, command)
    assert result.exit_code == 0, result.output
    clean = output.read_text(encoding="utf-8")
    for label in ("Missing asset", "Untranslated", "(original)", "not tracked", "coverage"):
        assert label not in clean
    assert json.loads(report_path_for(output).read_text())["show_status"] is False

    result = CliRunner().invoke(app, [*command, "--show-status"])
    assert result.exit_code == 0, result.output
    debug = output.read_text(encoding="utf-8")
    assert "Missing asset: images/gone.png" in debug
    assert "Untranslated — original text" in debug
    assert "(not tracked)" in debug
    assert json.loads(report_path_for(output).read_text())["show_status"] is True
