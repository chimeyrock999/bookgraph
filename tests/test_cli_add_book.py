from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app


def test_add_book_declares_contract_without_running_pipeline(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    pdf = tmp_path / "Designing Data-Intensive Applications.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    runner.invoke(app, ["init", str(workspace)])

    result = runner.invoke(app, ["add-book", str(workspace), str(pdf)])

    assert result.exit_code == 0
    manifest_path = (
        workspace
        / "sources"
        / "inbox"
        / "designing-data-intensive-applications"
        / "book.json"
    )
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest == {
        "book_id": "designing-data-intensive-applications",
        "title": "Designing Data-Intensive Applications",
        "source_type": "pdf",
        "source_path": str(pdf.resolve()),
        "workspace_path": str(workspace.resolve()),
        "status": "registered",
        "pdf": {
            "title": None,
            "author": None,
            "pages": 0,
            "has_bookmarks": False,
            "bookmarks": [],
        },
        "pipeline": {
            "parser": None,
            "segmenter": None,
            "wiki_backend": None,
        },
        "paths": {
            "book_root": str(manifest_path.parent),
            "original": str(manifest_path.parent / "original.pdf"),
            "parsed": str(
                workspace / "sources" / "parsed" / "designing-data-intensive-applications"
            ),
            "sections": str(
                workspace / "sources" / "sections" / "designing-data-intensive-applications"
            ),
            "wiki": str(workspace / "wiki" / "books" / "designing-data-intensive-applications"),
        },
    }
    assert (manifest_path.parent / "original.pdf").is_file()
    assert "Registered book designing-data-intensive-applications" in result.output
    assert "No parser or segmenter was run" in result.output


def test_add_book_requires_pdf_input(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    txt = tmp_path / "notes.txt"
    txt.write_text("not a pdf")
    runner.invoke(app, ["init", str(workspace)])

    result = runner.invoke(app, ["add-book", str(workspace), str(txt)])

    assert result.exit_code != 0
    assert "Only PDF input is supported by this CLI contract for now" in result.output


def test_add_book_can_dry_run_contract_without_copying(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    runner.invoke(app, ["init", str(workspace)])

    result = runner.invoke(app, ["add-book", str(workspace), str(pdf), "--dry-run"])

    assert result.exit_code == 0
    assert "Would register book" in result.output
    assert not (workspace / "sources" / "inbox" / "book").exists()
