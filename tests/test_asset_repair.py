from __future__ import annotations

import base64
import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.asset_repair import (
    ORIGINAL_REFERENCE_KEY,
    RECOVERED_FROM_KEY,
    repair_document_assets,
)
from bookgraph.cli import app
from bookgraph.documents import read_document, write_document
from bookgraph.exports.models import ASSET_MISSING
from bookgraph.exports.renderers import HtmlRenderer
from bookgraph.exports.translated import (
    build_translated_export,
    report_path_for,
    write_translated_export,
)
from bookgraph.parsers.markdown import document_from_markdown
from bookgraph.quality import ASSET_FILE_MISSING, document_quality_report, read_quality_report
from bookgraph.sections import read_sections, write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.translations import write_translation
from bookgraph.workspace import WorkspacePaths

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
OTHER_PNG = PNG + b"\x00"  # different bytes, same (lenient) image
DOC = "ddia"
REFERENCE = "assets/ddia_0307.png"
CAPTION = "Figure 3-7. Determining that Idaho is in North America."
SOURCE_MARKDOWN = f"""# Chapter 3

## Datalog: Recursive Relational Queries

Datalog is an even older language than SPARQL.

![{CAPTION}]({REFERENCE})

The rules can be combined recursively.
"""
# A translation normalised to the staged location, as in the issue.
TRANSLATION = f"""# Datalog: Truy vấn quan hệ đệ quy

Datalog là một ngôn ngữ còn lâu đời hơn SPARQL.

![Hình 3-7](sources/parsed/{DOC}/images/ddia_0307.png)

*Hình 3-7. Xác định rằng Idaho nằm ở Bắc Mỹ.*

Các quy tắc có thể được kết hợp đệ quy.
"""


def _ingest(tmp_path: Path, *, source_path: str | None = None) -> WorkspacePaths:
    """A parsed + segmented DDIA-like book whose figure file was never staged."""

    paths = WorkspacePaths(tmp_path / "ws")
    document = document_from_markdown(
        SOURCE_MARKDOWN,
        doc_id=DOC,
        fallback_title="DDIA",
        source_path=source_path or str(tmp_path / "ddia.md"),
        parser_name="markdown",
    )
    parsed_dir = paths.sources_parsed / DOC
    write_document(document, parsed_dir)
    sections = HeadingSegmenter(target_level=2).segment(document)
    write_sections(sections, paths.sources_sections / DOC)
    return paths


def _datalog(paths: WorkspacePaths):  # noqa: ANN202 - test helper
    sections = read_sections(paths.sources_sections / DOC / "sections.jsonl")
    return next(s for s in sections if "datalog" in s.id)


def test_missing_image_export_is_clean_and_report_captures_it(tmp_path: Path) -> None:
    paths = _ingest(tmp_path)
    section = _datalog(paths)
    write_translation(paths, section, "vi", TRANSLATION, includes_assets=True)

    export = build_translated_export(paths, DOC, lang="vi", generated_at="2026-10-02T00:00:00Z")
    output = paths.exports_root / f"{DOC}.vi-progress.html"
    report = write_translated_export(export, output, HtmlRenderer())

    html = output.read_text(encoding="utf-8")
    assert "Missing asset" not in html
    assert "missing-asset" not in html.split("</style>", 1)[1]
    assert "Hình 3-7. Xác định rằng Idaho nằm ở Bắc Mỹ." in html  # the caption survives
    assert "Các quy tắc có thể được kết hợp đệ quy." in html
    assert report.show_status is False

    payload = json.loads(report_path_for(output).read_text())
    missing = [w for w in payload["warnings"] if w["code"] == ASSET_MISSING]
    assert missing == [
        {
            "code": ASSET_MISSING,
            "message": missing[0]["message"],
            "section_id": section.id,
            "reference": f"sources/parsed/{DOC}/images/ddia_0307.png",
            "source_path": f"translations/vi/{DOC}/{section.id}.md",
            "block_id": None,
        }
    ]


def test_missing_image_in_original_section_keeps_caption_and_reports_block(
    tmp_path: Path,
) -> None:
    paths = _ingest(tmp_path)
    section = _datalog(paths)

    export = build_translated_export(paths, DOC, lang="vi", generated_at="2026-10-02T00:00:00Z")

    assert "Missing asset" not in export.html
    assert f"<figcaption>{CAPTION}</figcaption>" in export.html
    [warning] = [w for w in export.report.warnings if w.code == ASSET_MISSING]
    assert warning.section_id == section.id
    assert warning.reference == REFERENCE
    assert warning.source_path == f"sources/parsed/{DOC}/document.json"
    assert warning.block_id is not None

    debug = build_translated_export(
        paths, DOC, lang="vi", generated_at="2026-10-02T00:00:00Z", show_status=True
    )
    assert f"Missing asset: {REFERENCE}" in debug.html


def test_ingest_quality_reports_the_original_reference(tmp_path: Path) -> None:
    paths = _ingest(tmp_path)
    parsed_dir = paths.sources_parsed / DOC
    document = read_document(parsed_dir / "document.json")
    sections = read_sections(paths.sources_sections / DOC / "sections.jsonl")

    report = document_quality_report(DOC, sections, document.blocks, parsed_dir)

    [warning] = [w for w in report.warnings if w.code == ASSET_FILE_MISSING]
    assert warning.section_id == _datalog(paths).id
    assert warning.reference == REFERENCE
    assert REFERENCE in warning.message


def test_repair_recovers_a_file_left_in_the_parser_output(tmp_path: Path) -> None:
    paths = _ingest(tmp_path)
    parsed_dir = paths.sources_parsed / DOC
    leftover = parsed_dir / "raw" / "assets" / "ddia_0307.png"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(PNG)
    section = _datalog(paths)
    write_translation(paths, section, "vi", TRANSLATION, includes_assets=True)

    _, report = repair_document_assets(
        parsed_dir, sections=read_sections(paths.sources_sections / DOC / "sections.jsonl")
    )

    [repair] = report.repairs
    assert (repair.status, repair.recovered_path) == ("recovered", "images/ddia_0307.png")
    assert repair.section_ids == [section.id]
    assert (parsed_dir / "images" / "ddia_0307.png").read_bytes() == PNG
    block = next(b for b in read_document(parsed_dir / "document.json").blocks if b.type == "image")
    assert block.metadata["src"] == "images/ddia_0307.png"
    assert block.metadata[ORIGINAL_REFERENCE_KEY] == REFERENCE
    assert block.metadata[RECOVERED_FROM_KEY] == str(leftover)

    # Both the original block and the translation's normalised path now resolve, and the
    # translation stays fresh: section text was never touched.
    export = build_translated_export(paths, DOC, lang="vi", generated_at="2026-10-02T00:00:00Z")
    assert not [w for w in export.report.warnings if w.code == ASSET_MISSING]
    entry = next(e for e in export.report.sections if e.section_id == section.id)
    assert (entry.freshness, entry.assets_embedded) == ("fresh", 1)

    # A second run finds nothing left to repair.
    _, again = repair_document_assets(parsed_dir)
    assert again.missing == 0


def test_repair_recovers_from_the_source_epub(tmp_path: Path) -> None:
    epub = tmp_path / "ddia.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("OEBPS/assets/ddia_0307.png", PNG)
    paths = _ingest(tmp_path, source_path=str(epub))
    parsed_dir = paths.sources_parsed / DOC

    _, report = repair_document_assets(parsed_dir)

    [repair] = report.repairs
    assert repair.status == "recovered"
    assert repair.recovered_from == f"{epub}!OEBPS/assets/ddia_0307.png"
    assert (parsed_dir / "images" / "ddia_0307.png").read_bytes() == PNG


def test_repair_never_guesses_between_different_files(tmp_path: Path) -> None:
    paths = _ingest(tmp_path)
    parsed_dir = paths.sources_parsed / DOC
    for name, payload in (("a", PNG), ("b", OTHER_PNG)):
        path = parsed_dir / name / "ddia_0307.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(payload)
    before = (parsed_dir / "document.json").read_text()

    _, report = repair_document_assets(parsed_dir)

    [repair] = report.repairs
    assert repair.status == "ambiguous"
    assert len(repair.candidates) == 2
    assert (parsed_dir / "document.json").read_text() == before
    assert not (parsed_dir / "images").exists()


def test_repair_leaves_an_unrecoverable_reference_as_source_evidence(tmp_path: Path) -> None:
    paths = _ingest(tmp_path)
    parsed_dir = paths.sources_parsed / DOC
    before = (parsed_dir / "document.json").read_text()

    _, report = repair_document_assets(parsed_dir)

    assert [(r.status, r.reference) for r in report.repairs] == [("unrecoverable", REFERENCE)]
    assert report.recovered == 0
    assert (parsed_dir / "document.json").read_text() == before


def test_cli_repair_from_dir_refreshes_quality(tmp_path: Path) -> None:
    paths = _ingest(tmp_path)
    parsed_dir = paths.sources_parsed / DOC
    sections = read_sections(paths.sources_sections / DOC / "sections.jsonl")
    document = read_document(parsed_dir / "document.json")
    quality = paths.sources_sections / DOC / "quality.json"
    quality.write_text(
        document_quality_report(DOC, sections, document.blocks, parsed_dir).model_dump_json()
    )
    extra = tmp_path / "recovered-figures"
    extra.mkdir()
    (extra / "ddia_0307.png").write_bytes(PNG)
    runner = CliRunner()

    dry = runner.invoke(
        app, ["assets", "repair", str(paths.root), DOC, "--from", str(extra), "--dry-run"]
    )
    assert dry.exit_code == 0, dry.output
    assert "recovered: 1" in dry.output
    assert "repair: (dry run, nothing written)" in dry.output
    assert not (parsed_dir / "images").exists()

    result = runner.invoke(app, ["assets", "repair", str(paths.root), DOC, "--from", str(extra)])
    assert result.exit_code == 0, result.output
    assert f"{REFERENCE} -> images/ddia_0307.png" in result.output
    assert "(refreshed)" in result.output
    codes = [w.code for w in read_quality_report(quality).warnings]
    assert ASSET_FILE_MISSING not in codes


@pytest.mark.parametrize("flag", [[], ["--show-status"]])
def test_cli_export_reports_source_and_hides_placeholder(tmp_path: Path, flag: list[str]) -> None:
    paths = _ingest(tmp_path)

    result = CliRunner().invoke(
        app, ["export", "translated-pdf", str(paths.root), DOC, "--renderer", "html", *flag]
    )

    assert result.exit_code == 0, result.output
    assert f"(in sources/parsed/{DOC}/document.json)" in result.output
    html = (paths.exports_root / f"{DOC}.vi-progress.html").read_text(encoding="utf-8")
    assert ("Missing asset:" in html) == bool(flag)
