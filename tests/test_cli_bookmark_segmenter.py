from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app


def _init_workspace(tmp_path: Path) -> CliRunner:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return runner


def _write_book_manifest(workspace: Path, doc_id: str) -> None:
    inbox = workspace / "sources" / "inbox" / doc_id
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "book.json").write_text(
        json.dumps(
            {
                "book_id": doc_id,
                "pdf": {
                    "title": "Iceberg",
                    "author": None,
                    "pages": 6,
                    "has_bookmarks": True,
                    "bookmarks": [
                        {"title": "Chapter 1", "page_index": 1, "level": 1},
                        {"title": "Chapter 2", "page_index": 3, "level": 1},
                    ],
                },
            }
        )
    )


def _write_parsed_document(workspace: Path, doc_id: str) -> None:
    parsed_dir = workspace / "sources" / "parsed" / doc_id
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / "document.json").write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "title": "Iceberg",
                "blocks": [
                    {"id": "b0", "type": "text", "text": "Preface", "page_idx": 0, "order": 0},
                    {"id": "b1", "type": "text", "text": "One", "page_idx": 1, "order": 1},
                    {"id": "b2", "type": "text", "text": "Two", "page_idx": 3, "order": 2},
                ],
                "metadata": {"parser": "mineru-middle-json"},
            }
        )
    )


def test_segment_cli_uses_bookmark_segmenter_with_book_manifest(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_book_manifest(tmp_path, "iceberg")
    _write_parsed_document(tmp_path, "iceberg")

    result = runner.invoke(app, ["segment", str(tmp_path), "iceberg", "--segmenter", "bookmark"])

    assert result.exit_code == 0, result.output
    assert "segmenter: bookmark" in result.output
    assert "sections: 2" in result.output
    manifest = tmp_path / "sources" / "sections" / "iceberg" / "sections.jsonl"
    lines = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert [line["id"] for line in lines] == ["iceberg.chapter-1", "iceberg.chapter-2"]
    assert [line["text"] for line in lines] == ["One", "Two"]
