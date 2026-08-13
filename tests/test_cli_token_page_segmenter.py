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


def _write_parsed_document(workspace: Path, doc_id: str) -> None:
    parsed_dir = workspace / "sources" / "parsed" / doc_id
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / "document.json").write_text(
        json.dumps(
            {
                "doc_id": doc_id,
                "title": "A Paper",
                "blocks": [
                    {"id": "b1", "type": "text", "text": "alpha beta", "page_idx": 1},
                    {"id": "b2", "type": "text", "text": "gamma delta", "page_idx": 1},
                    {"id": "b3", "type": "text", "text": "epsilon zeta", "page_idx": 2},
                ],
                "metadata": {"parser": "markdown"},
            }
        )
    )


def test_segment_cli_uses_token_page_segmenter_with_token_budget(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_parsed_document(tmp_path, "paper")

    result = runner.invoke(
        app,
        [
            "segment",
            str(tmp_path),
            "paper",
            "--segmenter",
            "token-page",
            "--max-tokens",
            "4",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "segmenter: token-page" in result.output
    assert "max_tokens: 4" in result.output
    manifest = tmp_path / "sources" / "sections" / "paper" / "sections.jsonl"
    lines = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert [line["id"] for line in lines] == ["paper.part-1", "paper.part-2"]
    assert [line["block_ids"] for line in lines] == [["b1", "b2"], ["b3"]]


def test_segment_cli_rejects_invalid_max_tokens(tmp_path: Path) -> None:
    runner = _init_workspace(tmp_path)
    _write_parsed_document(tmp_path, "paper")

    result = runner.invoke(
        app,
        ["segment", str(tmp_path), "paper", "--segmenter", "token-page", "--max-tokens", "0"],
    )

    assert result.exit_code != 0
    assert "max_tokens must be at least 1" in result.output
