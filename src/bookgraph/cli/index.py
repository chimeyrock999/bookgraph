from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import index_app
from bookgraph.cli._shared import _validate_id
from bookgraph.documents import read_document
from bookgraph.index import IndexUnavailableError, default_index_backend
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


def _doc_title(workspace: WorkspacePaths, doc_id: str) -> str:
    """The parsed document title when available, else the doc id."""

    parsed = workspace.sources_parsed / doc_id / "document.json"
    if parsed.is_file():
        try:
            return read_document(parsed).title
        except (OSError, ValueError):
            pass
    return doc_id


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
    """Build the SQLite index (search + graph) under indexes/ from sections."""

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

    backend = default_index_backend()
    try:
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

            title = _doc_title(workspace, current_doc)
            count = backend.build_document(workspace, current_doc, title, sections)
            typer.echo(f"doc_id: {current_doc}")
            typer.echo(f"sections: {count}")
    except IndexUnavailableError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"backend: {backend.name}")
    typer.echo(f"index: {backend.location(workspace)}")
