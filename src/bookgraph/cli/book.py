from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.books import build_book_registration, register_book
from bookgraph.cli._app import app
from bookgraph.cli._shared import (
    _print_placeholder,
    _registered_original_path,
    _validate_id,
    _validate_plugin_name,
    _write_placeholder,
)
from bookgraph.defaults import default_parser_registry
from bookgraph.workspace import WorkspacePaths


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


@app.command("parse-book")
def parse_book(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    book_id: Annotated[
        str,
        typer.Argument(help="Registered book id from sources/inbox/<book_id>."),
    ],
    runner: Annotated[
        str,
        typer.Option("--runner", help="Runner requested for the future raw-source step."),
    ] = "mineru",
    runner_command: Annotated[
        str,
        typer.Option("--runner-command", help="Executable name for the future runner."),
    ] = "mineru",
    method: Annotated[
        str,
        typer.Option("--method", "-m", help="MinerU method reserved for the future runner."),
    ] = "auto",
    backend: Annotated[
        str | None,
        typer.Option(
            "--backend",
            "-b",
            help="Optional MinerU backend reserved for the future runner.",
        ),
    ] = None,
    timeout_seconds: Annotated[
        int | None,
        typer.Option(
            "--timeout-seconds",
            help="Timeout reserved for the future MinerU subprocess; pass 0 for no timeout.",
        ),
    ] = 3600,
    parser: Annotated[
        str,
        typer.Option(
            "--parser",
            "-p",
            help="Parser plugin reserved after runner output is staged.",
        ),
    ] = "mineru-middle-json",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the interface contract without writing files."),
    ] = False,
) -> None:
    """Declare the registered-book parse interface without invoking parser backends."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_book_id = _validate_id(book_id, "book_id")
    parser_name = _validate_plugin_name(default_parser_registry(), parser)
    book_manifest = workspace.sources_inbox / resolved_book_id / "book.json"
    original_source = _registered_original_path(workspace, resolved_book_id, book_manifest)
    parsed_dir = workspace.sources_parsed / resolved_book_id
    middle_json = parsed_dir / f"{resolved_book_id}_middle.json"
    if timeout_seconds is not None and timeout_seconds < 0:
        raise typer.BadParameter("timeout_seconds must be non-negative")
    resolved_timeout = None if timeout_seconds == 0 else timeout_seconds
    payload: dict[str, object] = {
        "command": "parse-book",
        "status": "placeholder",
        "book_id": resolved_book_id,
        "runner": {
            "name": runner,
            "command": runner_command,
            "method": method,
            "backend": backend,
            "timeout_seconds": resolved_timeout,
        },
        "parser": parser_name,
        "inputs": {
            "book_manifest": str(book_manifest),
            "original_source": str(original_source),
        },
        "intermediate_outputs": {
            "parsed_dir": str(parsed_dir),
            "middle_json": str(middle_json),
            "markdown": str(parsed_dir / f"{resolved_book_id}.md"),
            "layout_pdf": str(parsed_dir / f"{resolved_book_id}_layout.pdf"),
            "span_pdf": str(parsed_dir / f"{resolved_book_id}_span.pdf"),
            "content_list": str(parsed_dir / f"{resolved_book_id}_content_list.json"),
            "images_dir": str(parsed_dir / "images"),
        },
        "outputs": {"document": str(parsed_dir / "document.json")},
        "backend_not_run": True,
    }
    path = None if dry_run else _write_placeholder(
        workspace, f"parse-book-{resolved_book_id}", payload
    )
    _print_placeholder("parse-book", path)
