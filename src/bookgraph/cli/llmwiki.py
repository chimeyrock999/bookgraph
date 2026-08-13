from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import llmwiki_app
from bookgraph.workspace import WorkspacePaths


@llmwiki_app.command("serve")
def llmwiki_serve(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    print_command: Annotated[
        bool,
        typer.Option(
            "--print",
            help="Print the resolved llmwiki serve command instead of launching it.",
        ),
    ] = False,
) -> None:
    """Launch the optional llmwiki MCP server over the workspace's compiled wiki.

    This is a convenience wrapper that resolves the workspace's ``wiki/`` directory
    and runs ``llmwiki serve <wiki_dir>``. BookGraph MCP (``bookgraph mcp``) stays
    the primary reading server; this only serves the compiled wiki for
    search/query/context-pack workflows and never changes BookGraph's canonical
    inputs. See ``docs/mcp/llmwiki-integration.md``.
    """

    workspace = workspace_path.expanduser().resolve()
    if not workspace.is_dir():
        raise typer.BadParameter(f"Workspace not found: {workspace}. Run 'bookgraph init' first.")

    wiki_dir = WorkspacePaths(workspace).wiki_root
    # `bookgraph init` mkdir's the empty wiki/ skeleton, so directory existence is
    # not enough — require actual compiled content (any .md page) before serving.
    if not any(wiki_dir.rglob("*.md")):
        raise typer.BadParameter(
            f"No compiled wiki found under {wiki_dir}. Compile a wiki first, e.g. "
            "'bookgraph wiki compile <workspace> <doc_id>'."
        )

    command = ["llmwiki", "serve", str(wiki_dir)]
    if print_command:
        typer.echo(shlex.join(command))
        return

    if shutil.which("llmwiki") is None:
        raise typer.BadParameter(
            "llmwiki is not installed or not on PATH. Install the optional "
            "llm-wiki-compiler tool, or re-run with --print to see the command to run."
        )

    raise typer.Exit(subprocess.call(command))
