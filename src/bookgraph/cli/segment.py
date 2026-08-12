from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import app
from bookgraph.cli._shared import _validate_id, _validate_plugin_name
from bookgraph.defaults import default_segmenter_registry
from bookgraph.documents import read_document
from bookgraph.sections import write_sections
from bookgraph.workspace import WorkspacePaths


@app.command()
def segment(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Parsed document id from sources/parsed/<doc_id>.")],
    segmenter: Annotated[
        str,
        typer.Option(
            "--segmenter",
            "-s",
            help="Segmenter plugin name.",
        ),
    ] = "heading",
) -> None:
    """Segment a parsed document into human reading sections under sources/sections/."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    registry = default_segmenter_registry()
    segmenter_name = _validate_plugin_name(registry, segmenter)

    document_path = workspace.sources_parsed / resolved_doc_id / "document.json"
    if not document_path.is_file():
        raise typer.BadParameter(
            f"Parsed document not found: {document_path}. Run 'bookgraph parse' first."
        )

    try:
        document = read_document(document_path)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Invalid parsed document: {document_path}: {exc}") from exc
    sections = registry.get(segmenter_name).segment(document)
    output_dir = workspace.sources_sections / resolved_doc_id
    try:
        output = write_sections(sections, output_dir)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"segmenter: {segmenter_name}")
    typer.echo(f"doc_id: {resolved_doc_id}")
    typer.echo(f"sections: {len(sections)}")
    typer.echo(f"manifest: {output.manifest}")
