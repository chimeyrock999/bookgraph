from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app
from bookgraph.quality import read_quality_report


def _init_workspace(tmp_path: Path) -> CliRunner:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return runner


def _write_parsed_document(workspace: Path, doc_id: str, blocks: list[dict[str, object]]) -> None:
    parsed_dir = workspace / "sources" / "parsed" / doc_id
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / "document.json").write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "title": "Iceberg",
                "blocks": blocks,
                "metadata": {"parser": "mineru-middle-json"},
            }
        )
    )


def test_segment_writes_a_quality_report_and_echoes_warnings(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_parsed_document(
        tmp_path,
        "iceberg",
        [
            {"id": "b0", "type": "title", "text": "Snapshots", "level": 1, "page_idx": 3},
            {
                "id": "b1",
                "type": "table",
                "text": "Figure 6. Snapshot isolation.",
                "asset_path": "images/f6.jpg",
                "page_idx": 3,
            },
        ],
    )

    result = runner.invoke(app, ["segment", str(tmp_path), "iceberg", "--target-level", "1"])

    assert result.exit_code == 0, result.output
    report_path = tmp_path / "sources" / "sections" / "iceberg" / "quality.json"
    assert f"quality: {report_path}" in result.output
    assert "warnings: 2" in result.output
    # The anomalies are echoed inline, so an ingest run never hides them in the artifact.
    assert "asset_type_ambiguous" in result.output
    assert "asset_captions_only" in result.output

    report = read_quality_report(report_path)
    assert report.doc_id == "iceberg"
    assert report.section_count == 1
    assert report.warning_counts == {"asset_captions_only": 1, "asset_type_ambiguous": 1}
    ambiguous = next(w for w in report.warnings if w.code == "asset_type_ambiguous")
    assert (ambiguous.section_id, ambiguous.block_id) == ("iceberg.snapshots", "b1")


def test_segment_writes_a_clean_report_for_a_healthy_document(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_parsed_document(
        tmp_path,
        "iceberg",
        [
            {"id": "b0", "type": "title", "text": "Snapshots", "level": 1, "page_idx": 3},
            {
                "id": "b1",
                "type": "text",
                "text": "A real paragraph about snapshot isolation in table formats.",
                "page_idx": 3,
            },
        ],
    )

    result = runner.invoke(app, ["segment", str(tmp_path), "iceberg", "--target-level", "1"])

    assert result.exit_code == 0, result.output
    assert "warnings: 0" in result.output
    assert "warning:" not in result.output
    report = read_quality_report(tmp_path / "sources" / "sections" / "iceberg" / "quality.json")
    assert (report.warning_count, report.warnings) == (0, [])
