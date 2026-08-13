from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import index_app
from bookgraph.cli._shared import _validate_id
from bookgraph.indexes import build_section_index, index_path, write_index
from bookgraph.sections import read_sections
from bookgraph.utils import ID_PATTERN
from bookgraph.workspace import WorkspacePaths


def _segmented_doc_ids(workspace: WorkspacePaths) -> list[str]:
    root = workspace.sources_sections
    return sorted(
        child.name
        for child in (root.iterdir() if root.is_dir() else [])
        if (child / "sections.jsonl").is_file() and ID_PATTERN.fullmatch(child.name)
    )


@index_app.command("build")
def index_build(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[
        str | None,
        typer.Option(
            "--doc-id",
            help="Index only this document. Defaults to every segmented document.",
        ),
    ] = None,
) -> None:
    """Build the search index under indexes/sections/<doc_id>.json from sections."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    if doc_id is not None:
        doc_ids = [_validate_id(doc_id, "doc_id")]
    else:
        doc_ids = _segmented_doc_ids(workspace)
        if not doc_ids:
            raise typer.BadParameter(
                f"No segmented documents under {workspace.sources_sections}. "
                "Run 'bookgraph segment' first."
            )

    for current_doc in doc_ids:
        manifest = workspace.sources_sections / current_doc / "sections.jsonl"
        if not manifest.is_file():
            raise typer.BadParameter(
                f"Sections manifest not found: {manifest}. Run 'bookgraph segment' first."
            )
        try:
            sections = read_sections(manifest)
        except (OSError, ValueError) as exc:
            raise typer.BadParameter(f"Invalid sections manifest: {manifest}: {exc}") from exc

        index = build_section_index(current_doc, sections)
        path = write_index(index, index_path(workspace, current_doc))
        typer.echo(f"doc_id: {current_doc}")
        typer.echo(f"sections: {len(index.sections)}")
        typer.echo(f"terms: {len(index.postings)}")
        typer.echo(f"index: {path}")
