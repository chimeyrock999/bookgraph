from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import app
from bookgraph.cli._config import load_config
from bookgraph.cli._shared import _validate_id, _validate_plugin_name
from bookgraph.defaults import default_segmenter_registry
from bookgraph.documents import read_document
from bookgraph.sections import write_sections
from bookgraph.segmenters.bookmark import BookmarkSegmenter, PdfBookmark
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.workspace import WorkspacePaths


@app.command()
def segment(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Parsed document id from sources/parsed/<doc_id>.")],
    segmenter: Annotated[
        str | None,
        typer.Option(
            "--segmenter",
            "-s",
            help="Segmenter plugin name. Defaults to [segmenter].default.",
        ),
    ] = None,
    target_level: Annotated[
        int | None,
        typer.Option(
            "--target-level",
            help=(
                "Heading levels at or above this number start new sections. "
                "Defaults to [segmenter].target_level."
            ),
        ),
    ] = None,
) -> None:
    """Segment a parsed document into human reading sections under sources/sections/."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    config = load_config(workspace)
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    registry = default_segmenter_registry()
    segmenter_name = _validate_plugin_name(registry, segmenter or config.segmenter.default)
    resolved_target_level = config.segmenter.target_level if target_level is None else target_level
    if resolved_target_level < 1:
        raise typer.BadParameter("target_level must be at least 1")

    document_path = workspace.sources_parsed / resolved_doc_id / "document.json"
    if not document_path.is_file():
        raise typer.BadParameter(
            f"Parsed document not found: {document_path}. Run 'bookgraph parse' first."
        )

    try:
        document = read_document(document_path)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Invalid parsed document: {document_path}: {exc}") from exc
    segmenter_plugin = registry.get(segmenter_name)
    if isinstance(segmenter_plugin, HeadingSegmenter):
        segmenter_plugin = HeadingSegmenter(target_level=resolved_target_level)
    if isinstance(segmenter_plugin, BookmarkSegmenter):
        segmenter_plugin = BookmarkSegmenter(
            bookmarks=_load_bookmarks(workspace, resolved_doc_id),
            split_level=resolved_target_level,
        )
    sections = segmenter_plugin.segment(document)
    output_dir = workspace.sources_sections / resolved_doc_id
    try:
        output = write_sections(sections, output_dir)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"segmenter: {segmenter_name}")
    typer.echo(f"target_level: {resolved_target_level}")
    typer.echo(f"doc_id: {resolved_doc_id}")
    typer.echo(f"sections: {len(sections)}")
    typer.echo(f"manifest: {output.manifest}")


def _load_bookmarks(workspace: WorkspacePaths, doc_id: str) -> list[PdfBookmark]:
    manifest = workspace.sources_inbox / doc_id / "book.json"
    if not manifest.is_file():
        return []
    try:
        payload = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    pdf = payload.get("pdf") if isinstance(payload, dict) else None
    raw_bookmarks = pdf.get("bookmarks") if isinstance(pdf, dict) else None
    if not isinstance(raw_bookmarks, list):
        return []
    bookmarks: list[PdfBookmark] = []
    for raw in raw_bookmarks:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title")
        page_index = raw.get("page_index")
        level = raw.get("level")
        if not isinstance(title, str) or not title.strip():
            continue
        if page_index is not None and not isinstance(page_index, int):
            continue
        if not isinstance(level, int):
            continue
        bookmarks.append(PdfBookmark(title=title.strip(), page_index=page_index, level=level))
    return bookmarks
