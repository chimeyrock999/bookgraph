from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.books import build_book_registration, register_book
from bookgraph.cli._app import app
from bookgraph.cli._config import load_config
from bookgraph.cli._shared import (
    _print_placeholder,
    _registered_original_path,
    _validate_id,
    _validate_plugin_name,
    _write_placeholder,
)
from bookgraph.defaults import default_parser_registry
from bookgraph.documents import write_document
from bookgraph.parsers.errors import UnsupportedSourceError
from bookgraph.parsers.markitdown import MissingParserDependencyError
from bookgraph.parsers.mineru_runner import MinerUNotInstalledError, MinerURunError, MinerURunner
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
        str | None,
        typer.Option("--runner", help="Raw-source runner. Defaults to [mineru].runner."),
    ] = None,
    runner_command: Annotated[
        str | None,
        typer.Option("--runner-command", help="Executable name. Defaults to [mineru].command."),
    ] = None,
    method: Annotated[
        str | None,
        typer.Option("--method", "-m", help="MinerU method. Defaults to [mineru].method."),
    ] = None,
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
            help="MinerU subprocess timeout; pass 0 for no timeout. Defaults to config.",
        ),
    ] = None,
    parser: Annotated[
        str | None,
        typer.Option(
            "--parser",
            "-p",
            help="Parser plugin after runner output is staged. Defaults to [parsers].default_pdf.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the interface contract without writing files."),
    ] = False,
) -> None:
    """Run the registered-book parse pipeline: raw PDF runner then parser."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    config = load_config(workspace)
    resolved_book_id = _validate_id(book_id, "book_id")
    resolved_runner = runner or config.mineru.runner
    resolved_runner_command = runner_command or config.mineru.command
    resolved_method = method or config.mineru.method
    resolved_backend = backend if backend is not None else config.mineru.backend
    resolved_timeout = config.mineru.timeout_seconds if timeout_seconds is None else timeout_seconds
    if resolved_timeout is not None and resolved_timeout < 0:
        raise typer.BadParameter("timeout_seconds must be non-negative")
    resolved_timeout = None if resolved_timeout == 0 else resolved_timeout
    parser_name = _validate_plugin_name(
        default_parser_registry(), parser or config.parsers.default_pdf
    )
    book_manifest = workspace.sources_inbox / resolved_book_id / "book.json"
    original_source = _registered_original_path(workspace, resolved_book_id, book_manifest)
    parsed_dir = workspace.sources_parsed / resolved_book_id
    middle_json = parsed_dir / f"{resolved_book_id}_middle.json"
    payload: dict[str, object] = {
        "command": "parse-book",
        "status": "placeholder",
        "book_id": resolved_book_id,
        "runner": {
            "name": resolved_runner,
            "command": resolved_runner_command,
            "method": resolved_method,
            "backend": resolved_backend,
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
    if dry_run:
        path = _write_placeholder(workspace, f"parse-book-{resolved_book_id}", payload)
        _print_placeholder("parse-book", path)
        return

    if resolved_runner != "mineru":
        raise typer.BadParameter(f"Unknown runner: {resolved_runner}. Available: mineru")
    if not original_source.is_file():
        raise typer.BadParameter(f"Registered original source not found: {original_source}")

    try:
        run_result = MinerURunner(
            name=resolved_runner,
            command=resolved_runner_command,
            method=resolved_method,
            backend=resolved_backend,
            timeout_seconds=resolved_timeout,
        ).run(original_source, parsed_dir)
        parser_plugin = default_parser_registry().get(parser_name)
        document = parser_plugin.parse(run_result.middle_json, parsed_dir)
    except (
        UnsupportedSourceError,
        MissingParserDependencyError,
        MinerUNotInstalledError,
        MinerURunError,
        ValueError,
    ) as exc:
        raise typer.BadParameter(str(exc)) from exc

    if document.doc_id != resolved_book_id:
        document = type(document).model_validate(
            {**document.model_dump(), "doc_id": resolved_book_id}
        )
    document = type(document).model_validate(
        {
            **document.model_dump(),
            "metadata": {
                **document.metadata,
                "runner": resolved_runner,
                "runner_command": resolved_runner_command,
            },
        }
    )
    document_path = write_document(document, parsed_dir)
    typer.echo(f"runner: {resolved_runner}")
    typer.echo(f"parser: {parser_name}")
    typer.echo(f"doc_id: {document.doc_id}")
    typer.echo(f"title: {document.title}")
    typer.echo(f"blocks: {len(document.blocks)}")
    typer.echo(f"document: {document_path}")
