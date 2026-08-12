from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(
    help="BookGraph: pluggable document-to-graph-wiki pipeline.",
    invoke_without_command=True,
)

PROJECT_DIRS = [
    "sources/inbox",
    "sources/parsed",
    "sources/sections",
    "wiki/concepts",
    "wiki/comparisons",
    "wiki/daily",
    "indexes",
    "reading_plans",
    "runs",
]

DEFAULT_CONFIG = """# BookGraph workspace config

[parsers]
default_pdf = "mineru"
default_office = "markitdown"

[segmenter]
default = "heading"
target_level = 2

[wiki]
backend = "llmwiki"

[mcp]
server = "bookgraph"
"""


@app.command()
def init(path: Annotated[Path, typer.Argument(help="Workspace directory to initialize.")]) -> None:
    """Create the pluggable BookGraph workspace layout."""

    path.mkdir(parents=True, exist_ok=True)
    for rel in PROJECT_DIRS:
        (path / rel).mkdir(parents=True, exist_ok=True)
    config = path / "bookgraph.toml"
    if not config.exists():
        config.write_text(DEFAULT_CONFIG)
    typer.echo(f"Initialized BookGraph workspace at {path}")
