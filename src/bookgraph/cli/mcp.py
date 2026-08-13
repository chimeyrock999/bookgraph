from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import app


@app.command()
def mcp(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
) -> None:
    """Serve the workspace over MCP (stdio): reading, search, and graph/context tools."""

    try:
        from bookgraph.mcp.server import create_server
    except ModuleNotFoundError as exc:
        raise typer.BadParameter(
            "The MCP server needs the 'mcp' extra. Install it with: uv sync --extra mcp"
        ) from exc

    workspace = workspace_path.expanduser().resolve()
    if not workspace.is_dir():
        raise typer.BadParameter(f"Workspace not found: {workspace}. Run 'bookgraph init' first.")

    server = create_server(workspace)
    server.run()
