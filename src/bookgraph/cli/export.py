from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import export_app
from bookgraph.cli._shared import _validate_id
from bookgraph.exports.models import FALLBACK_POLICIES, ExportReport, FallbackPolicy
from bookgraph.exports.renderers import (
    AUTO_RENDERER,
    RenderError,
    default_renderer_registry,
    select_renderer,
)
from bookgraph.exports.translated import (
    ExportError,
    UntranslatedSectionsError,
    build_translated_export,
    report_path_for,
    write_translated_export,
)
from bookgraph.workspace import WorkspacePaths


def _print_report(report: ExportReport) -> None:
    typer.echo(f"doc_id: {report.doc_id}")
    typer.echo(f"lang: {report.lang}")
    typer.echo(f"fallback: {report.fallback}")
    typer.echo(
        f"coverage: {report.translated_sections}/{report.total_sections} "
        f"({report.coverage * 100:.1f}%)"
    )
    for warning in report.warnings:
        typer.echo(f"warning: {warning.code}: {warning.section_id}: {warning.message}")


@export_app.command("translated-pdf")
def export_translated_pdf(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Sectioned document id.")],
    lang: Annotated[
        str, typer.Option("--lang", help="Translation language code, e.g. 'vi'.")
    ] = "vi",
    fallback: Annotated[
        str,
        typer.Option(
            "--fallback",
            help="Untranslated sections: 'original' (source text), 'skip' (placeholder), "
            "or 'fail' (exit non-zero).",
        ),
    ] = "original",
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Output file; relative paths are under the workspace. "
            "Defaults to exports/<doc_id>.<lang>-progress.pdf (.html for --renderer html).",
        ),
    ] = None,
    renderer_name: Annotated[
        str,
        typer.Option(
            "--renderer",
            help="auto | weasyprint | playwright | html. 'auto' picks HTML for a .html "
            "output, else the first installed PDF backend.",
        ),
    ] = AUTO_RENDERER,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Fail instead of exporting when any asset is missing."),
    ] = False,
    check: Annotated[
        bool,
        typer.Option("--check", help="Preflight only: print coverage and warnings, write nothing."),
    ] = False,
) -> None:
    """Export a partially translated book as one reading PDF with original-text fallback."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    resolved_lang = _validate_id(lang, "lang")
    if fallback not in FALLBACK_POLICIES:
        raise typer.BadParameter(
            f"--fallback must be one of: {', '.join(FALLBACK_POLICIES)}", param_hint="--fallback"
        )
    policy: FallbackPolicy = fallback  # type: ignore[assignment]

    registry = default_renderer_registry()
    if renderer_name != AUTO_RENDERER and renderer_name not in registry.names():
        raise typer.BadParameter(
            f"Unknown renderer: {renderer_name}. Available: "
            f"{AUTO_RENDERER}, {', '.join(registry.names())}",
            param_hint="--renderer",
        )
    suffix = ".html" if renderer_name == "html" else ".pdf"
    output = out or Path("exports") / f"{resolved_doc_id}.{resolved_lang}-progress{suffix}"
    if not output.is_absolute():
        output = workspace.root / output

    try:
        export = build_translated_export(
            workspace, resolved_doc_id, lang=resolved_lang, fallback=policy
        )
    except UntranslatedSectionsError as exc:
        _print_report(exc.report)
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ExportError as exc:
        raise typer.BadParameter(str(exc)) from exc

    _print_report(export.report)
    if check:
        typer.echo("export: (check only, not written)")
        return

    try:
        renderer = select_renderer(registry, renderer_name, output)
        report = write_translated_export(export, output, renderer, strict=strict)
    except (ExportError, RenderError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"renderer: {report.renderer}")
    typer.echo(f"export: {output}")
    typer.echo(f"report: {report_path_for(output)}")
