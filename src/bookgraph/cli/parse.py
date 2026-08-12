from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import app
from bookgraph.cli._shared import _doc_id_for_source, _validate_id
from bookgraph.defaults import default_parser_registry
from bookgraph.documents import write_document
from bookgraph.parsers.markitdown import MissingParserDependencyError
from bookgraph.parsers.routing import UnsupportedSourceError, select_parser_name
from bookgraph.workspace import WorkspacePaths


@app.command()
def parsers() -> None:
    """List available parser plugins."""

    for name in default_parser_registry().names():
        typer.echo(name)


@app.command()
def parse(
    source: Annotated[Path, typer.Argument(help="Source document to parse.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Workspace root (default: current directory)."),
    ] = None,
    parser: Annotated[
        str | None,
        typer.Option("--parser", "-p", help="Parser plugin name; detected from file type."),
    ] = None,
    doc_id: Annotated[
        str | None,
        typer.Option("--doc-id", help="Override the document id used for output paths and ids."),
    ] = None,
) -> None:
    """Parse a source document into canonical blocks under sources/parsed/."""

    source_path = source.expanduser().resolve()
    if not source_path.is_file():
        raise typer.BadParameter(f"Source file not found: {source_path}")

    workspace = WorkspacePaths((output or Path.cwd()).expanduser().resolve())
    registry = default_parser_registry()
    try:
        parser_name = parser or select_parser_name(source_path)
        plugin = registry.get(parser_name)
    except UnsupportedSourceError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except KeyError as exc:
        available = ", ".join(registry.names())
        raise typer.BadParameter(f"{exc.args[0]} Available: {available}") from exc

    resolved_doc_id = _validate_id(doc_id, "doc_id") if doc_id else _doc_id_for_source(source_path)
    parsed_dir = workspace.sources_parsed / resolved_doc_id
    try:
        document = plugin.parse(source_path, parsed_dir)
    except MissingParserDependencyError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if document.doc_id != resolved_doc_id:
        document = type(document).model_validate(
            {**document.model_dump(), "doc_id": resolved_doc_id}
        )

    document_path = write_document(document, parsed_dir)

    typer.echo(f"parser: {parser_name}")
    typer.echo(f"doc_id: {document.doc_id}")
    typer.echo(f"title: {document.title}")
    typer.echo(f"blocks: {len(document.blocks)}")
    typer.echo(f"document: {document_path}")
