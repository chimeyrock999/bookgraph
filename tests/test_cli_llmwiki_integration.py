"""End-to-end bridge → compile → status integration against the real llmwiki CLI.

Skipped unless the optional ``llm-wiki-compiler`` tool is installed and on PATH,
so it exercises the real v1.1 contract wherever the tool is available (CI images
with the optional extra, local dev machines) without becoming a hard dependency
of the BookGraph test suite.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bookgraph.cli import app

runner = CliRunner()

pytestmark = pytest.mark.skipif(
    shutil.which("llmwiki") is None,
    reason="optional llm-wiki-compiler ('llmwiki') is not installed",
)


def _write_sections_manifest(workspace: Path, doc_id: str) -> None:
    sections_dir = workspace / "sources" / "sections" / doc_id
    sections_dir.mkdir(parents=True)
    (sections_dir / "sections.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "id": f"{doc_id}.{slug}",
                    "doc_id": doc_id,
                    "title": title,
                    "level": 2,
                    "heading_path": [title],
                    "page_start": None,
                    "page_end": None,
                    "text": body,
                    "prev_id": None,
                    "next_id": None,
                    "block_ids": [],
                    "metadata": {},
                }
            )
            for slug, title, body in [
                ("intro", "Introduction", "Deep work is focused, undistracted cognition."),
                ("rules", "The Rules", "Work deeply, embrace boredom, quit social media."),
            ]
        )
        + "\n"
    )


def _llmwiki_status(workspace: Path) -> dict:
    proc = subprocess.run(
        ["llmwiki", "status", "--root", str(workspace), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        # Fall back to a plain status invocation if this build lacks --json.
        proc = subprocess.run(
            ["llmwiki", "status", "--root", str(workspace)],
            capture_output=True,
            text=True,
            check=False,
        )
    return json.loads(proc.stdout)


def test_bridge_then_compile_produces_a_non_empty_llmwiki_project(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    assert runner.invoke(app, ["init", str(workspace)]).exit_code == 0
    _write_sections_manifest(workspace, "deep-work")

    result = runner.invoke(
        app, ["llmwiki", "bridge", str(workspace), "deep-work", "--compile"]
    )

    assert result.exit_code == 0, result.output
    # llmwiki compiled a project: state + at least one compiled page exist, and the
    # bridged sources are non-empty (no full-book truncation to worry about here).
    assert (workspace / ".llmwiki" / "state.json").is_file()
    status = _llmwiki_status(workspace)
    assert status["sources"] > 0
    assert status["pages"]["total"] > 0
