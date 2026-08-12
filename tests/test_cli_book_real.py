from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bookgraph.cli import app


def _fake_mineru_bin(bin_dir: Path) -> Path:
    script = bin_dir / "fake-mineru"
    script.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

argv = sys.argv[1:]
pdf = Path(argv[argv.index('-p') + 1])
out = Path(argv[argv.index('-o') + 1])
method = argv[argv.index('-m') + 1]
backend = argv[argv.index('-b') + 1] if '-b' in argv else None
auto = out / pdf.stem / 'auto'
auto.mkdir(parents=True)
(auto / f'{pdf.stem}_middle.json').write_text(json.dumps({
    'pdf_info': [{
        'page_idx': 0,
        'para_blocks': [
            {'type': 'title', 'lines': [{'spans': [{'content': 'Deep Work'}]}]},
            {
                'type': 'text',
                'lines': [{'spans': [{'content': f'method={method} backend={backend}'}]}],
            },
        ],
    }]
}))
(auto / f'{pdf.stem}.md').write_text('# Deep Work\\n')
"""
    )
    script.chmod(0o755)
    return script


def test_parse_book_runs_mineru_then_parser_and_writes_document(
    tmp_path: Path, monkeypatch: object
) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    pdf = tmp_path / "Deep Work.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mineru = _fake_mineru_bin(bin_dir)

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    assert runner.invoke(app, ["add-book", str(workspace), str(pdf)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "parse-book",
            str(workspace),
            "deep-work",
            "--runner-command",
            str(fake_mineru),
            "--method",
            "ocr",
            "--backend",
            "pipeline",
        ],
    )

    assert result.exit_code == 0, result.output
    parsed_dir = workspace / "sources" / "parsed" / "deep-work"
    document_path = parsed_dir / "document.json"
    document = json.loads(document_path.read_text())
    assert document["doc_id"] == "deep-work"
    assert document["title"] == "Deep Work"
    assert document["metadata"] == {
        "parser": "mineru-middle-json",
        "source_path": str(parsed_dir / "deep-work_middle.json"),
        "runner": "mineru",
        "runner_command": str(fake_mineru),
    }
    assert [block["text"] for block in document["blocks"]] == [
        "Deep Work",
        "method=ocr backend=pipeline",
    ]
    assert (parsed_dir / "deep-work_middle.json").is_file()
    assert (parsed_dir / "deep-work.md").is_file()
    assert not (parsed_dir / "_mineru").exists()
    assert not (workspace / "runs" / "cli-placeholders" / "parse-book-deep-work.json").exists()
    assert "runner: mineru" in result.output
    assert "parser: mineru-middle-json" in result.output
    assert f"document: {document_path}" in result.output


def test_parse_book_uses_mineru_config_defaults(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    pdf = tmp_path / "Deep Work.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mineru = _fake_mineru_bin(bin_dir)

    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    config = workspace / "bookgraph.toml"
    config.write_text(
        config.read_text()
        + f'\n[mineru]\ncommand = "{fake_mineru}"\nmethod = "ocr"\nbackend = "pipeline"\n'
    )
    assert runner.invoke(app, ["add-book", str(workspace), str(pdf)]).exit_code == 0

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work"])

    assert result.exit_code == 0, result.output
    document_path = workspace / "sources" / "parsed" / "deep-work" / "document.json"
    document = json.loads(document_path.read_text())
    assert document["blocks"][1]["text"] == "method=ocr backend=pipeline"


def test_parse_book_requires_registered_original_source(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["parse-book", str(workspace), "missing-book"])

    assert result.exit_code != 0
    assert "Registered original source not found" in result.output
    assert not (workspace / "sources" / "parsed" / "missing-book").exists()


def test_parse_book_dry_run_keeps_placeholder_contract(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0

    result = runner.invoke(app, ["parse-book", str(workspace), "deep-work", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert (workspace / "runs" / "cli-placeholders" / "parse-book-deep-work.json").is_file()
    assert not (workspace / "sources" / "parsed" / "deep-work").exists()
    assert "Backend not run" in result.output
