from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

import typer

from bookgraph.books import build_book_registration, register_book
from bookgraph.defaults import (
    default_parser_registry,
    default_segmenter_registry,
    default_wiki_backend_registry,
)
from bookgraph.documents import read_document, write_document
from bookgraph.parsers.markitdown import MissingParserDependencyError
from bookgraph.parsers.routing import UnsupportedSourceError, select_parser_name
from bookgraph.plugins import PluginRegistry
from bookgraph.sections import write_sections
from bookgraph.utils import doc_id_from_path, validate_slug_id
from bookgraph.workspace import WorkspacePaths, default_config

app = typer.Typer(help="BookGraph: pluggable document-to-graph-wiki pipeline.")
wiki_app = typer.Typer(help="Wiki backend command interfaces.")
reading_plan_app = typer.Typer(help="Reading-plan command interfaces.")
app.add_typer(wiki_app, name="wiki")
app.add_typer(reading_plan_app, name="reading-plan")

_SOURCE_TYPE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_id(value: str, field_name: str) -> str:
    try:
        return validate_slug_id(value, field_name=field_name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _validate_plugin_name(registry: PluginRegistry[Any], name: str) -> str:
    try:
        registry.get(name)
    except KeyError as exc:
        available = ", ".join(registry.names())
        raise typer.BadParameter(f"{exc.args[0]} Available: {available}") from exc
    return name


def _registered_original_path(workspace: WorkspacePaths, book_id: str, manifest: Path) -> Path:
    source_type = "pdf"
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise typer.BadParameter(f"Invalid book manifest: {manifest}") from exc
        raw_source_type = payload.get("source_type")
        if isinstance(raw_source_type, str):
            if not _SOURCE_TYPE_PATTERN.fullmatch(raw_source_type):
                raise typer.BadParameter(f"Invalid source_type in book manifest: {raw_source_type}")
            source_type = raw_source_type
    return workspace.sources_inbox / book_id / f"original.{source_type}"


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
            return validate_slug_id(book_id, field_name="book_id")
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


def _write_placeholder(workspace: WorkspacePaths, name: str, payload: dict[str, object]) -> Path:
    output_dir = workspace.runs_root / "cli-placeholders"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def _print_placeholder(name: str, path: Path | None) -> None:
    typer.echo(f"Interface: {name}")
    if path is not None:
        typer.echo(f"Placeholder: {path}")
    typer.echo("Backend not run.")


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


@app.command()
def segment(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Parsed document id from sources/parsed/<doc_id>.")],
    segmenter: Annotated[
        str,
        typer.Option(
            "--segmenter",
            "-s",
            help="Segmenter plugin name.",
        ),
    ] = "heading",
) -> None:
    """Segment a parsed document into human reading sections under sources/sections/."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    registry = default_segmenter_registry()
    segmenter_name = _validate_plugin_name(registry, segmenter)

    document_path = workspace.sources_parsed / resolved_doc_id / "document.json"
    if not document_path.is_file():
        raise typer.BadParameter(
            f"Parsed document not found: {document_path}. Run 'bookgraph parse' first."
        )

    try:
        document = read_document(document_path)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Invalid parsed document: {document_path}: {exc}") from exc
    sections = registry.get(segmenter_name).segment(document)
    output_dir = workspace.sources_sections / resolved_doc_id
    try:
        output = write_sections(sections, output_dir)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"segmenter: {segmenter_name}")
    typer.echo(f"doc_id: {resolved_doc_id}")
    typer.echo(f"sections: {len(sections)}")
    typer.echo(f"manifest: {output.manifest}")


@wiki_app.command("compile")
def wiki_compile(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Sectioned document id.")],
    backend: Annotated[
        str,
        typer.Option("--backend", "-b", help="Wiki backend requested for the future backend."),
    ] = "llmwiki",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the interface contract without writing files."),
    ] = False,
) -> None:
    """Declare the wiki compile interface without invoking wiki backends."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    backend_name = _validate_plugin_name(default_wiki_backend_registry(), backend)
    payload: dict[str, object] = {
        "command": "wiki compile",
        "status": "placeholder",
        "doc_id": resolved_doc_id,
        "backend": backend_name,
        "inputs": {
            "sections_manifest": str(
                workspace.sources_sections / resolved_doc_id / "sections.jsonl"
            )
        },
        "outputs": {"wiki_book_dir": str(workspace.wiki_books / resolved_doc_id)},
        "backend_not_run": True,
    }
    path = None if dry_run else _write_placeholder(
        workspace, f"wiki-compile-{resolved_doc_id}", payload
    )
    _print_placeholder("wiki compile", path)


@reading_plan_app.command("create")
def reading_plan_create(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Sectioned document id.")],
    plan_id: Annotated[
        str | None,
        typer.Option("--plan-id", help="Reading plan id; defaults to the doc id."),
    ] = None,
    daily_sections: Annotated[
        int,
        typer.Option("--daily-sections", help="Requested sections per daily reading tick."),
    ] = 1,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the interface contract without writing files."),
    ] = False,
) -> None:
    """Declare the reading-plan create interface without building progress state."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    resolved_plan_id = _validate_id(plan_id or resolved_doc_id, "plan_id")
    if daily_sections < 1:
        raise typer.BadParameter("daily_sections must be at least 1")
    payload: dict[str, object] = {
        "command": "reading-plan create",
        "status": "placeholder",
        "doc_id": resolved_doc_id,
        "plan_id": resolved_plan_id,
        "daily_sections": daily_sections,
        "inputs": {
            "sections_manifest": str(
                workspace.sources_sections / resolved_doc_id / "sections.jsonl"
            )
        },
        "outputs": {"reading_plan": str(workspace.reading_plans_root / f"{resolved_plan_id}.json")},
        "backend_not_run": True,
    }
    path = None if dry_run else _write_placeholder(
        workspace, f"reading-plan-create-{resolved_plan_id}", payload
    )
    _print_placeholder("reading-plan create", path)


@reading_plan_app.command("next")
def reading_plan_next(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    plan_id: Annotated[str, typer.Argument(help="Reading plan id.")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the interface contract without writing files."),
    ] = False,
) -> None:
    """Declare the reading-plan next-section interface without reading progress state."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_plan_id = _validate_id(plan_id, "plan_id")
    payload: dict[str, object] = {
        "command": "reading-plan next",
        "status": "placeholder",
        "plan_id": resolved_plan_id,
        "inputs": {"reading_plan": str(workspace.reading_plans_root / f"{resolved_plan_id}.json")},
        "outputs": {"context_pack": None},
        "backend_not_run": True,
    }
    path = None if dry_run else _write_placeholder(
        workspace, f"reading-plan-next-{resolved_plan_id}", payload
    )
    _print_placeholder("reading-plan next", path)


@reading_plan_app.command("mark-read")
def reading_plan_mark_read(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    plan_id: Annotated[str, typer.Argument(help="Reading plan id.")],
    section_id: Annotated[
        str | None,
        typer.Option("--section-id", help="Specific section id; defaults to current section."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the interface contract without writing files."),
    ] = False,
) -> None:
    """Declare the mark-read interface without mutating progress state."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_plan_id = _validate_id(plan_id, "plan_id")
    payload: dict[str, object] = {
        "command": "reading-plan mark-read",
        "status": "placeholder",
        "plan_id": resolved_plan_id,
        "section_id": section_id,
        "inputs": {"reading_plan": str(workspace.reading_plans_root / f"{resolved_plan_id}.json")},
        "outputs": {"reading_plan": str(workspace.reading_plans_root / f"{resolved_plan_id}.json")},
        "backend_not_run": True,
    }
    path = None if dry_run else _write_placeholder(
        workspace, f"reading-plan-mark-read-{resolved_plan_id}", payload
    )
    _print_placeholder("reading-plan mark-read", path)
