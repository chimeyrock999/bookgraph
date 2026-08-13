from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import reading_plan_app
from bookgraph.cli._config import load_config
from bookgraph.cli._shared import _validate_id
from bookgraph.models import ReadingPlan
from bookgraph.reading_plans import (
    create_reading_plan,
    mark_section_read,
    next_sections,
    read_reading_plan,
    write_reading_plan,
)
from bookgraph.sections import read_sections
from bookgraph.workspace import WorkspacePaths


def _plan_path(workspace: WorkspacePaths, plan_id: str) -> Path:
    return workspace.reading_plans_root / f"{plan_id}.json"


def _load_plan(workspace: WorkspacePaths, plan_id: str) -> tuple[Path, ReadingPlan]:
    path = _plan_path(workspace, plan_id)
    if not path.is_file():
        raise typer.BadParameter(
            f"Reading plan not found: {path}. Run 'bookgraph reading-plan create' first."
        )
    try:
        plan = read_reading_plan(path)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Invalid reading plan: {path}: {exc}") from exc
    return path, plan


@reading_plan_app.command("create")
def reading_plan_create(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Sectioned document id.")],
    plan_id: Annotated[
        str | None,
        typer.Option("--plan-id", help="Reading plan id; defaults to the doc id."),
    ] = None,
    daily_sections: Annotated[
        int | None,
        typer.Option(
            "--daily-sections",
            help="Sections per daily reading tick. Defaults to [reading_plan].daily_sections.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Compute the plan and print it without writing files."),
    ] = False,
) -> None:
    """Create a reading plan from a document's sections manifest."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    config = load_config(workspace)
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    resolved_plan_id = _validate_id(plan_id or resolved_doc_id, "plan_id")
    resolved_daily_sections = (
        config.reading_plan.daily_sections if daily_sections is None else daily_sections
    )
    if resolved_daily_sections < 1:
        raise typer.BadParameter("daily_sections must be at least 1")

    manifest = workspace.sources_sections / resolved_doc_id / "sections.jsonl"
    if not manifest.is_file():
        raise typer.BadParameter(
            f"Sections manifest not found: {manifest}. Run 'bookgraph segment' first."
        )
    try:
        sections = read_sections(manifest)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Invalid sections manifest: {manifest}: {exc}") from exc

    try:
        plan = create_reading_plan(
            sections,
            plan_id=resolved_plan_id,
            doc_id=resolved_doc_id,
            daily_sections=resolved_daily_sections,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"plan_id: {plan.plan_id}")
    typer.echo(f"doc_id: {plan.doc_id}")
    typer.echo(f"daily_sections: {plan.daily_sections}")
    typer.echo(f"sections: {len(plan.section_ids)}")
    if dry_run:
        typer.echo("reading_plan: (dry run, not written)")
        return
    path = write_reading_plan(plan, _plan_path(workspace, resolved_plan_id))
    typer.echo(f"reading_plan: {path}")


@reading_plan_app.command("list")
def reading_plan_list(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
) -> None:
    """List reading plans in the workspace with their completion progress."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    root = workspace.reading_plans_root
    paths = sorted(root.glob("*.json")) if root.is_dir() else []
    printed = 0
    for path in paths:
        try:
            plan = read_reading_plan(path)
        except (OSError, ValueError):
            continue  # skip an unreadable/corrupt plan rather than fail the listing
        typer.echo(
            f"{plan.plan_id}\t{plan.doc_id}\t{len(plan.completed)}/{len(plan.section_ids)}"
        )
        printed += 1
    if printed == 0:
        typer.echo("reading_plans: (none)")


@reading_plan_app.command("next")
def reading_plan_next(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    plan_id: Annotated[str, typer.Argument(help="Reading plan id.")],
) -> None:
    """Print the next unread sections for a reading plan without mutating it."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_plan_id = _validate_id(plan_id, "plan_id")
    _, plan = _load_plan(workspace, resolved_plan_id)
    pack = next_sections(plan)

    typer.echo(f"plan_id: {pack.plan_id}")
    typer.echo(f"doc_id: {pack.doc_id}")
    typer.echo(f"next: {', '.join(pack.sections) if pack.sections else '(complete)'}")
    typer.echo(f"remaining: {pack.remaining}")


@reading_plan_app.command("mark-read")
def reading_plan_mark_read(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    plan_id: Annotated[str, typer.Argument(help="Reading plan id.")],
    section_id: Annotated[
        str | None,
        typer.Option("--section-id", help="Specific section id; defaults to the next unread one."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print what would be marked without writing files."),
    ] = False,
) -> None:
    """Mark a section read and persist the updated reading plan."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_plan_id = _validate_id(plan_id, "plan_id")
    path, plan = _load_plan(workspace, resolved_plan_id)

    # section_id is a lookup key against the plan's own section ids (which contain
    # dots, e.g. ``<doc_id>.<slug>``), never a filename, so it is validated by
    # membership in the store rather than as a bare slug.
    try:
        updated, marked = mark_section_read(plan, section_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo(f"plan_id: {updated.plan_id}")
    typer.echo(f"marked: {marked}")
    typer.echo(f"completed: {len(updated.completed)}/{len(updated.section_ids)}")
    if dry_run:
        typer.echo("reading_plan: (dry run, not written)")
        return
    write_reading_plan(updated, path)
    typer.echo(f"reading_plan: {path}")
