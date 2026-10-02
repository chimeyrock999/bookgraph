"""``bookgraph export translated-pdf`` writing EPUB (``--out *.epub`` / ``--renderer epub``)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.cli import app
from bookgraph.models import Section
from bookgraph.sections import write_sections
from bookgraph.workspace import WorkspacePaths

DOC = "tiny"


def _workspace(tmp_path: Path, translation: str = "# Một\n\nTiếng Việt.\n") -> WorkspacePaths:
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
    body = paths.translations_root / "vi" / DOC / f"{DOC}.one.md"
    body.parent.mkdir(parents=True)
    body.write_text(translation, encoding="utf-8")
    return paths


def _export(tmp_path: Path, *args: str):
    return CliRunner().invoke(app, ["export", "translated-pdf", str(tmp_path), DOC, *args])


@pytest.mark.parametrize("mode", ["translated", "bilingual"])
def test_an_epub_out_writes_an_epub_in_both_modes(tmp_path: Path, mode: str) -> None:
    _workspace(tmp_path)
    output = tmp_path / "book.epub"

    result = _export(tmp_path, "--mode", mode, "--out", str(output), "--source-lang", "en")

    assert result.exit_code == 0, result.output
    assert "renderer: epub" in result.output
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist()[0] == "mimetype"
        chapters = sorted(n for n in archive.namelist() if n.startswith("OEBPS/chapter-"))
    assert chapters == ["OEBPS/chapter-001.xhtml", "OEBPS/chapter-002.xhtml"]
    payload = json.loads((tmp_path / "book.report.json").read_text())
    assert (payload["renderer"], payload["mode"], payload["source_lang"]) == ("epub", mode, "en")


def test_renderer_epub_defaults_to_an_epub_file(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)

    result = _export(tmp_path, "--renderer", "epub")

    assert result.exit_code == 0, result.output
    assert (paths.exports_root / f"{DOC}.vi-progress.epub").is_file()


def test_epub_renderer_refuses_another_suffix(tmp_path: Path) -> None:
    _workspace(tmp_path)

    epub_as_pdf = _export(tmp_path, "--renderer", "epub", "--out", str(tmp_path / "x.pdf"))
    html_as_epub = _export(tmp_path, "--renderer", "html", "--out", str(tmp_path / "x.epub"))

    assert epub_as_pdf.exit_code == 2
    assert "writes .epub files" in epub_as_pdf.output
    assert html_as_epub.exit_code == 2
    assert not (tmp_path / "x.pdf").exists() and not (tmp_path / "x.epub").exists()


def test_xhtml_repairs_are_printed_after_writing(tmp_path: Path) -> None:
    _workspace(tmp_path, "# Một\n\n<div><b>đậm</div>\n")

    checked = _export(tmp_path, "--out", str(tmp_path / "book.epub"), "--check")
    written = _export(tmp_path, "--out", str(tmp_path / "book.epub"))

    assert "xhtml_repaired" not in checked.output  # found while writing only
    assert written.exit_code == 0, written.output
    assert "warning: xhtml_repaired: tiny.one: " in written.output
    assert "closed unclosed <b>" in written.output


def test_invalid_source_lang_is_rejected(tmp_path: Path) -> None:
    _workspace(tmp_path)

    result = _export(tmp_path, "--source-lang", "../en", "--check")

    assert result.exit_code == 2
    assert "--source-lang" in result.output
