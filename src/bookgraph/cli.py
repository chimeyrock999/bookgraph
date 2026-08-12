from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from bookgraph.books import build_book_registration, register_book
from bookgraph.defaults import default_parser_registry
from bookgraph.documents import write_document
from bookgraph.parsers.markitdown import MissingParserDependencyError
from bookgraph.parsers.routing import UnsupportedSourceError, select_parser_name
from bookgraph.utils import doc_id_from_path
from bookgraph.workspace import WorkspacePaths, default_config

app = typer.Typer(help="BookGraph: pluggable document-to-graph-wiki pipeline.")


def _resolve_workspace_path(path: Path | None, output: Path | None) -> Path:
    if path is not None and output is not None:
        raise typer.BadParameter("Use either PATH or --output, not both.")
    if path is None and output is None:
        raise typer.BadParameter("Workspace path is required. Pass PATH or --output.")
    return (output or path).expanduser().resolve()  # type: ignore[union-attr]


@app.command()
def init(
    path: Annotated[
        Path | None,
        typer.Argument(help="Workspace directory to initialize."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Alias for the workspace/output root path."),
    ] = None,
) -> None:
    """Create the pluggable BookGraph workspace layout."""

    workspace_path = _resolve_workspace_path(path, output)
    workspace = WorkspacePaths(workspace_path)
    workspace.root.mkdir(parents=True, exist_ok=True)
    for directory in workspace.directories():
        directory.mkdir(parents=True, exist_ok=True)
    if not workspace.config.exists():
        workspace.config.write_text(default_config(workspace))
    typer.echo(f"Initialized BookGraph workspace at {workspace.root}")


@app.command("add-book")
def add_book(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    pdf_path: Annotated[Path, typer.Argument(help="PDF book path to register.")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the registration contract without writing files."),
    ] = False,
) -> None:
    """Register a PDF book in the workspace without running parsers or segmenters."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    try:
        registration = build_book_registration(workspace, pdf_path)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if dry_run:
        typer.echo(f"Would register book {registration.book_id}")
        typer.echo(f"Manifest: {registration.manifest_path}")
        typer.echo("No parser or segmenter was run.")
        return

    register_book(registration)
    typer.echo(f"Registered book {registration.book_id}")
    typer.echo(f"Manifest: {registration.manifest_path}")
    typer.echo("No parser or segmenter was run.")


@app.command()
def paths(
    path: Annotated[Path, typer.Argument(help="Workspace/output root path.")],
) -> None:
    """Print canonical output paths for a BookGraph workspace."""

    workspace = WorkspacePaths(path.expanduser().resolve())
    for name, location in workspace.as_mapping().items():
        typer.echo(f"{name}: {location}")


def _doc_id_for_source(source: Path) -> str:
    """Prefer a registered book id so parser output lands in the book's directory."""

    manifest = source.parent / "book.json"
    if manifest.is_file():
        try:
            book_id = json.loads(manifest.read_text()).get("book_id")
        except (OSError, json.JSONDecodeError, AttributeError):
            book_id = None
        if isinstance(book_id, str) and book_id:
            return book_id
    return doc_id_from_path(source)


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

    resolved_doc_id = doc_id or _doc_id_for_source(source_path)
    parsed_dir = workspace.sources_parsed / resolved_doc_id
    try:
        document = plugin.parse(source_path, parsed_dir)
    except MissingParserDependencyError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if document.doc_id != resolved_doc_id:
        document = document.model_copy(update={"doc_id": resolved_doc_id})

    document_path = write_document(document, parsed_dir)

    typer.echo(f"parser: {parser_name}")
    typer.echo(f"doc_id: {document.doc_id}")
    typer.echo(f"title: {document.title}")
    typer.echo(f"blocks: {len(document.blocks)}")
    typer.echo(f"document: {document_path}")
