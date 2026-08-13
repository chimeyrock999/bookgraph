from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import wiki_app
from bookgraph.cli._config import load_config
from bookgraph.cli._shared import (
    _print_placeholder,
    _validate_id,
    _validate_plugin_name,
    _write_placeholder,
)
from bookgraph.defaults import default_wiki_backend_registry
from bookgraph.sections import read_sections
from bookgraph.workspace import WorkspacePaths


@wiki_app.command("compile")
def wiki_compile(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Sectioned document id.")],
    backend: Annotated[
        str | None,
        typer.Option("--backend", "-b", help="Wiki backend. Defaults to [wiki].backend."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the interface contract without writing files."),
    ] = False,
) -> None:
    """Compile section manifests into a wiki backend output."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    config = load_config(workspace)
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    backend_name = _validate_plugin_name(
        default_wiki_backend_registry(), backend or config.wiki.backend
    )
    sections_manifest = workspace.sources_sections / resolved_doc_id / "sections.jsonl"
    wiki_book_dir = workspace.wiki_books / resolved_doc_id
    payload: dict[str, object] = {
        "command": "wiki compile",
        "status": "placeholder",
        "doc_id": resolved_doc_id,
        "backend": backend_name,
        "inputs": {"sections_manifest": str(sections_manifest)},
        "outputs": {"wiki_book_dir": str(wiki_book_dir)},
        "backend_not_run": True,
    }
    if dry_run:
        path = _write_placeholder(workspace, f"wiki-compile-{resolved_doc_id}", payload)
        _print_placeholder("wiki compile", path)
        return

    if not sections_manifest.is_file():
        raise typer.BadParameter(f"Sections manifest not found: {sections_manifest}")
    try:
        sections = read_sections(sections_manifest)
        backend_plugin = default_wiki_backend_registry().get(backend_name)
        output_dir = backend_plugin.compile_book(sections, wiki_book_dir)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"backend: {backend_name}")
    typer.echo(f"doc_id: {resolved_doc_id}")
    typer.echo(f"sections: {len(sections)}")
    typer.echo(f"wiki: {output_dir}")
