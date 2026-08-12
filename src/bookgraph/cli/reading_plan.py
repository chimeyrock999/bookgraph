from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import reading_plan_app
from bookgraph.cli._shared import _print_placeholder, _validate_id, _write_placeholder
from bookgraph.workspace import WorkspacePaths


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
