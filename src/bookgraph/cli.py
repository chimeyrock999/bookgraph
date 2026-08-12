from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.books import build_book_registration, register_book
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
