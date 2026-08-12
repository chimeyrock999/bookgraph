from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import typer

from bookgraph.plugins import PluginRegistry
from bookgraph.utils import doc_id_from_path, validate_slug_id
from bookgraph.workspace import WorkspacePaths

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
