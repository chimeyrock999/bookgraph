from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import export_app
from bookgraph.cli._shared import _validate_id
from bookgraph.exports.models import (
    EXPORT_MODES,
    FALLBACK_POLICIES,
    ExportMode,
    ExportReport,
    ExportWarning,
    FallbackPolicy,
)
from bookgraph.exports.renderers import (
    AUTO_RENDERER,
    RenderError,
    bilingual_layout_for,
    check_output_suffix,
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
from bookgraph.translations import validate_lang
from bookgraph.workspace import WorkspacePaths


def _print_report(report: ExportReport) -> None:
    typer.echo(f"doc_id: {report.doc_id}")
    typer.echo(f"lang: {report.lang}")
    typer.echo(f"mode: {report.mode}")
    typer.echo(f"fallback: {report.fallback}")
    typer.echo(
        f"coverage: {report.translated_sections}/{report.total_sections} "
        f"({report.coverage * 100:.1f}%)"
    )
    _print_warnings(report.warnings)


def _print_warnings(warnings: list[ExportWarning]) -> None:
    for warning in warnings:
        line = f"warning: {warning.code}: {warning.section_id}: {warning.describe()}"
        if warning.source_path:
            line += f" (in {warning.source_path})"
        typer.echo(line)


@export_app.command("translated-pdf")
def export_translated_pdf(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Sectioned document id.")],
    lang: Annotated[
        str, typer.Option("--lang", help="Translation language code, e.g. 'vi'.")
    ] = "vi",
    source_lang: Annotated[
        str | None,
        typer.Option(
            "--source-lang",
            help="Language of the original text, e.g. 'en', used to tag original-language "
            "content for readers (else 'und').",
        ),
    ] = None,
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="'translated' (translation where available, else the fallback) or "
            "'bilingual' (original | translated side by side; one after the other in EPUB).",
        ),
    ] = "translated",
    fallback: Annotated[
        str,
        typer.Option(
            "--fallback",
            help="Untranslated sections: 'original' (source text), 'skip' (placeholder), "
            "or 'fail' (exit non-zero). In bilingual mode this fills the right column.",
        ),
    ] = "original",
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Output file; relative paths are under the workspace. "
            "Defaults to exports/<doc_id>.<lang>-progress.pdf, or <lang>-bilingual.pdf "
            "with --mode bilingual (.html for --renderer html, .epub for --renderer epub).",
        ),
    ] = None,
    renderer_name: Annotated[
        str,
        typer.Option(
            "--renderer",
            help="auto | weasyprint | playwright | html | epub. 'auto' picks HTML for a "
            ".html output, EPUB for a .epub output, else the first installed PDF backend.",
        ),
    ] = AUTO_RENDERER,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Fail instead of exporting when an asset is missing, a translation is "
            "stale, a translation left out the section's figures/tables, or a translation "
            "changed its section's link targets, anchors, or paths. In bilingual mode this "
            "includes original-column assets of translated sections, reported apart as "
            "'[original column]' source-asset problems.",
        ),
    ] = False,
    show_status: Annotated[
        bool,
        typer.Option(
            "--show-status",
            help="Debug: also print translation status, fallback notes, coverage and "
            "missing-asset placeholders on the reading pages (by default they are only "
            "in the report).",
        ),
    ] = False,
    check: Annotated[
        bool,
        typer.Option("--check", help="Preflight only: print coverage and warnings, write nothing."),
    ] = False,
) -> None:
    """Export a partially translated book as one reading PDF, HTML page or EPUB,
    monolingual or bilingual."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    resolved_doc_id = _validate_id(doc_id, "doc_id")
    try:
        resolved_lang = validate_lang(lang)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--lang") from exc
    try:
        resolved_source_lang = validate_lang(source_lang) if source_lang is not None else None
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--source-lang") from exc
    if fallback not in FALLBACK_POLICIES:
        raise typer.BadParameter(
            f"--fallback must be one of: {', '.join(FALLBACK_POLICIES)}", param_hint="--fallback"
        )
    policy: FallbackPolicy = fallback  # type: ignore[assignment]
    if mode not in EXPORT_MODES:
        raise typer.BadParameter(
            f"--mode must be one of: {', '.join(EXPORT_MODES)}", param_hint="--mode"
        )
    export_mode: ExportMode = mode  # type: ignore[assignment]

    registry = default_renderer_registry()
    if renderer_name != AUTO_RENDERER and renderer_name not in registry.names():
        raise typer.BadParameter(
            f"Unknown renderer: {renderer_name}. Available: "
            f"{AUTO_RENDERER}, {', '.join(registry.names())}",
            param_hint="--renderer",
        )
    suffix = {"html": ".html", "epub": ".epub"}.get(renderer_name, ".pdf")
    variant = "bilingual" if export_mode == "bilingual" else "progress"
    output = out or Path("exports") / f"{resolved_doc_id}.{resolved_lang}-{variant}{suffix}"
    if not output.is_absolute():
        output = workspace.root / output
    try:
        check_output_suffix(registry, renderer_name, output)
    except RenderError as exc:
        raise typer.BadParameter(str(exc), param_hint="--out") from exc

    try:
        export = build_translated_export(
            workspace,
            resolved_doc_id,
            lang=resolved_lang,
            fallback=policy,
            mode=export_mode,
            show_status=show_status,
            bilingual_layout=bilingual_layout_for(registry, renderer_name, output),
            source_lang=resolved_source_lang,
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
        # A preflight must fail exactly when the real export would be refused.
        if strict and export.report.strict_warnings:
            typer.echo(f"error: {export.report.strict_summary()}", err=True)
            raise typer.Exit(code=1)
        return

    try:
        renderer = select_renderer(registry, renderer_name, output)
        report = write_translated_export(export, output, renderer, strict=strict)
    except (ExportError, RenderError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # Problems found while writing (EPUB's XHTML repairs), after the ones printed above.
    _print_warnings(report.warnings[len(export.report.warnings) :])
    typer.echo(f"renderer: {report.renderer}")
    typer.echo(f"export: {output}")
    typer.echo(f"report: {report_path_for(output)}")
