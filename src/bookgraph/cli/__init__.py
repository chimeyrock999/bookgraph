"""BookGraph Typer CLI.

The CLI is split into one module per pipeline stage. Each command module
decorates the shared ``app`` (or a sub-app) from :mod:`bookgraph.cli._app`, so
importing them here registers every command as a side effect. ``app`` stays the
single public entry point (``bookgraph.cli:app``).
"""

from __future__ import annotations

# Import for side effects: each module registers its commands on ``app``.
from bookgraph.cli import (  # noqa: E402,F401
    book,
    index,
    mcp,
    parse,
    reading_plan,
    segment,
    wiki,
    workspace_cmds,
)
from bookgraph.cli._app import app

__all__ = ["app"]
