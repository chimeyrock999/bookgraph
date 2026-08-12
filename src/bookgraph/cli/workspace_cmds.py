from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import app
from bookgraph.cli._shared import _resolve_workspace_path
from bookgraph.workspace import WorkspacePaths, default_config


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


@app.command()
def paths(
    path: Annotated[Path, typer.Argument(help="Workspace/output root path.")],
) -> None:
    """Print canonical output paths for a BookGraph workspace."""

    workspace = WorkspacePaths(path.expanduser().resolve())
    for name, location in workspace.as_mapping().items():
        typer.echo(f"{name}: {location}")
