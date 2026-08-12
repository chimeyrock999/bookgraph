from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from bookgraph.cli import app


def _init_workspace(tmp_path: Path) -> CliRunner:
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return runner


def _failure_text(result: Any) -> str:
    return f"{result.output or ''}\n{result.exception or ''}"


def test_parse_writes_canonical_document_into_workspace(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    source = tmp_path / "sources" / "inbox" / "Deep Work.md"
    source.write_text("# Deep Work\n\nOpening paragraph.\n")

    result = runner.invoke(app, ["parse", str(source), "--output", str(tmp_path)])

    assert result.exit_code == 0, result.output
    document_path = tmp_path / "sources" / "parsed" / "deep-work" / "document.json"
    payload = json.loads(document_path.read_text())
    assert payload["doc_id"] == "deep-work"
    assert payload["title"] == "Deep Work"
    assert [block["type"] for block in payload["blocks"]] == ["title", "text"]
    assert payload["metadata"]["parser"] == "markdown"
    assert "parser: markdown" in result.output
    assert "blocks: 2" in result.output


def test_parse_honors_an_explicit_parser_override(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    source = tmp_path / "sources" / "inbox" / "clip.txt"
    source.write_text("# Clip\n\nBody text.\n")

    result = runner.invoke(
        app, ["parse", str(source), "--output", str(tmp_path), "--parser", "markdown"]
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "sources" / "parsed" / "clip" / "document.json").is_file()
    assert "parser: markdown" in result.output


def test_parse_rejects_pdf_without_an_explicit_parser(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    source = tmp_path / "sources" / "inbox" / "ddia.pdf"
    source.write_bytes(b"%PDF-1.7")

    result = runner.invoke(app, ["parse", str(source), "--output", str(tmp_path)])

    assert result.exit_code != 0
    assert "MinerU" in _failure_text(result)
    assert not (tmp_path / "sources" / "parsed" / "ddia").exists()


def test_parse_rejects_unknown_parser_names(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    source = tmp_path / "sources" / "inbox" / "notes.md"
    source.write_text("# Notes\n")

    result = runner.invoke(
        app, ["parse", str(source), "--output", str(tmp_path), "--parser", "nope"]
    )

    assert result.exit_code != 0
    assert "Unknown parser plugin: nope" in _failure_text(result)


def test_parse_reports_a_missing_source_file(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)

    result = runner.invoke(
        app, ["parse", str(tmp_path / "sources" / "inbox" / "ghost.md"), "--output", str(tmp_path)]
    )

    assert result.exit_code != 0
    assert "ghost.md" in _failure_text(result)


def test_parse_uses_registered_book_id_for_output_layout(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    book_root = tmp_path / "sources" / "inbox" / "deep-work"
    book_root.mkdir(parents=True, exist_ok=True)
    (book_root / "book.json").write_text(json.dumps({"book_id": "deep-work"}))
    source = book_root / "original.md"
    source.write_text("# Deep Work\n\nBody.\n")

    result = runner.invoke(app, ["parse", str(source), "--output", str(tmp_path)])

    assert result.exit_code == 0, result.output
    document_path = tmp_path / "sources" / "parsed" / "deep-work" / "document.json"
    assert json.loads(document_path.read_text())["doc_id"] == "deep-work"


def test_parse_accepts_an_explicit_doc_id_override(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    source = tmp_path / "sources" / "inbox" / "original.md"
    source.write_text("# Deep Work\n\nBody.\n")

    result = runner.invoke(
        app, ["parse", str(source), "--output", str(tmp_path), "--doc-id", "ddia"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads((tmp_path / "sources" / "parsed" / "ddia" / "document.json").read_text())
    assert payload["doc_id"] == "ddia"


def test_parse_ingests_mineru_middle_json(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    source = tmp_path / "sources" / "inbox" / "ddia_middle.json"
    source.write_text(
        json.dumps(
            {
                "pdf_info": [
                    {
                        "page_idx": 4,
                        "para_blocks": [
                            {
                                "type": "title",
                                "bbox": [0, 0, 10, 10],
                                "lines": [{"spans": [{"content": "Chapter 3. Storage"}]}],
                            },
                            {
                                "type": "text",
                                "lines": [{"spans": [{"content": "Opening paragraph."}]}],
                            },
                        ],
                    }
                ]
            }
        )
    )

    result = runner.invoke(app, ["parse", str(source), "--output", str(tmp_path)])

    assert result.exit_code == 0, result.output
    payload = json.loads((tmp_path / "sources" / "parsed" / "ddia" / "document.json").read_text())
    assert payload["doc_id"] == "ddia"
    assert payload["title"] == "Chapter 3. Storage"
    assert [block["type"] for block in payload["blocks"]] == ["title", "text"]
    assert payload["blocks"][0]["page_idx"] == 4


def test_parsers_command_lists_registered_plugins(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["parsers"])

    assert result.exit_code == 0, result.output
    assert result.output.split() == ["markdown", "markitdown", "mineru-middle-json"]
