from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.asset_repair import AssetRepairReport, repair_document_assets
from bookgraph.cli._app import assets_app
from bookgraph.cli._shared import _validate_id
from bookgraph.quality import document_quality_report, write_quality_report
from bookgraph.sections import read_sections
from bookgraph.workspace import WorkspacePaths


@assets_app.command("repair")
def assets_repair(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Parsed document id.")],
    search_dirs: Annotated[
        list[Path] | None,
        typer.Option(
            "--from",
            help="Extra directory to look for missing asset files in (repeatable), after "
            "the parser output and the source EPUB.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would be recovered; write nothing."),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the repair report as JSON.")
    ] = False,
) -> None:
    """Recover parsed image/table files that a document references but never staged."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    parsed_dir = workspace.sources_parsed / resolved_doc_id
    if not (parsed_dir / "document.json").is_file():
        raise typer.BadParameter(
            f"Parsed document not found: {parsed_dir / 'document.json'}. "
            "Run 'bookgraph parse' first."
        )
    dirs = [path.expanduser().resolve() for path in search_dirs or []]
    for directory in dirs:
        if not directory.is_dir():
            raise typer.BadParameter(f"Not a directory: {directory}", param_hint="--from")

    sections_dir = workspace.sources_sections / resolved_doc_id
    manifest = sections_dir / "sections.jsonl"
    try:
        sections = read_sections(manifest) if manifest.is_file() else []
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Invalid sections manifest: {manifest}: {exc}") from exc

    document, report = repair_document_assets(
        parsed_dir, sections=sections, search_dirs=dirs, dry_run=dry_run
    )
    quality_path: Path | None = None
    if report.recovered and not dry_run and sections:
        # The ingest report judged these assets missing; re-judge them against the
        # repaired document so quality.json and get_section agree again.
        quality_path = write_quality_report(
            document_quality_report(resolved_doc_id, sections, document.blocks, parsed_dir),
            sections_dir,
        )

    if as_json:
        typer.echo(report.model_dump_json(indent=2))
        return
    _print_report(report)
    if quality_path is not None:
        typer.echo(f"quality: {quality_path} (refreshed)")
    if dry_run:
        typer.echo("repair: (dry run, nothing written)")


def _print_report(report: AssetRepairReport) -> None:
    typer.echo(f"doc_id: {report.doc_id}")
    typer.echo(f"missing: {report.missing}")
    typer.echo(f"recovered: {report.recovered}")
    for repair in report.repairs:
        where = f" [{', '.join(repair.section_ids)}]" if repair.section_ids else ""
        if repair.status == "recovered":
            typer.echo(
                f"recovered: {repair.block_id}{where}: {repair.reference} -> "
                f"{repair.recovered_path} (from {repair.recovered_from})"
            )
        elif repair.status == "ambiguous":
            typer.echo(
                f"ambiguous: {repair.block_id}{where}: {repair.reference}: "
                f"{len(repair.candidates)} candidate files, none used: "
                + ", ".join(repair.candidates)
            )
        else:
            typer.echo(f"unrecoverable: {repair.block_id}{where}: {repair.reference}")
