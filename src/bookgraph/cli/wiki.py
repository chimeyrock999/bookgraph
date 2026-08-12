from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import wiki_app
from bookgraph.cli._shared import (
    _print_placeholder,
    _validate_id,
    _validate_plugin_name,
    _write_placeholder,
)
from bookgraph.defaults import default_wiki_backend_registry
from bookgraph.workspace import WorkspacePaths


@wiki_app.command("compile")
def wiki_compile(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Sectioned document id.")],
    backend: Annotated[
        str,
        typer.Option("--backend", "-b", help="Wiki backend requested for the future backend."),
    ] = "llmwiki",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the interface contract without writing files."),
    ] = False,
) -> None:
    """Declare the wiki compile interface without invoking wiki backends."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    backend_name = _validate_plugin_name(default_wiki_backend_registry(), backend)
    payload: dict[str, object] = {
        "command": "wiki compile",
        "status": "placeholder",
        "doc_id": resolved_doc_id,
        "backend": backend_name,
        "inputs": {
            "sections_manifest": str(
                workspace.sources_sections / resolved_doc_id / "sections.jsonl"
            )
        },
        "outputs": {"wiki_book_dir": str(workspace.wiki_books / resolved_doc_id)},
        "backend_not_run": True,
    }
    path = None if dry_run else _write_placeholder(
        workspace, f"wiki-compile-{resolved_doc_id}", payload
    )
    _print_placeholder("wiki compile", path)
