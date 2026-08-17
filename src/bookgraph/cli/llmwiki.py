from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import llmwiki_app
from bookgraph.cli._shared import _validate_id
from bookgraph.llmwiki_bridge import stage_sections
from bookgraph.reading_plans import read_reading_plan
from bookgraph.sections import read_sections
from bookgraph.workspace import WorkspacePaths


def _resolve_workspace(workspace_path: Path) -> WorkspacePaths:
    workspace = workspace_path.expanduser().resolve()
    if not workspace.is_dir():
        raise typer.BadParameter(f"Workspace not found: {workspace}. Run 'bookgraph init' first.")
    return WorkspacePaths(workspace)


def _run_llmwiki(command: list[str]) -> None:
    """Launch an ``llmwiki`` subcommand, forwarding its exit code.

    Shared by ``serve`` and ``bridge --compile`` so a future change (better error,
    timeout, env passthrough) is made once. Fails with an actionable message when
    ``llmwiki`` is not installed rather than a bare ``FileNotFoundError``.
    """

    if shutil.which("llmwiki") is None:
        raise typer.BadParameter(
            "llmwiki is not installed or not on PATH. Install the optional "
            "llm-wiki-compiler tool, or re-run with --print to see the command to run."
        )
    raise typer.Exit(subprocess.call(command))


@llmwiki_app.command("serve")
def llmwiki_serve(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    print_command: Annotated[
        bool,
        typer.Option(
            "--print",
            help="Print the resolved llmwiki serve command instead of launching it.",
        ),
    ] = False,
) -> None:
    """Launch the optional llmwiki MCP server over the workspace's llmwiki project.

    Convenience wrapper that runs ``llmwiki serve --root <workspace>/llmwiki`` —
    the real ``llm-wiki-compiler`` v1.1 contract, which takes ``--root <project>``
    and has no positional root argument. The llmwiki project lives in its own
    ``llmwiki/`` subtree (isolated from BookGraph's ``wiki/`` and ``sources/``);
    the compiler ingests the sources staged by ``bookgraph llmwiki bridge`` and
    serves its compiled pages.

    BookGraph MCP (``bookgraph mcp``) stays the primary reading server; this only
    serves the derived, compiled llmwiki projection and never changes BookGraph's
    canonical inputs. See ``docs/mcp/llmwiki-integration.md``.
    """

    paths = _resolve_workspace(workspace_path)
    command = ["llmwiki", "serve", "--root", str(paths.llmwiki_root)]

    if print_command:
        # Pure command generation (e.g. to paste into an MCP client config): emit
        # it with shell-safe quoting without requiring a staged/compiled project.
        typer.echo(shlex.join(command))
        return

    # Staging alone is not enough — llmwiki serves an empty project until a compile
    # has run, so require the compile-state marker before launching.
    if not paths.llmwiki_state.is_file():
        raise typer.BadParameter(
            f"No compiled llmwiki project at {paths.llmwiki_root} "
            f"({paths.llmwiki_state} missing). Bridge and compile a book first, e.g. "
            "'bookgraph llmwiki bridge <workspace> <doc_id> --compile'."
        )

    _run_llmwiki(command)


@llmwiki_app.command("bridge")
def llmwiki_bridge(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[str, typer.Argument(help="Sectioned document id.")],
    plan_id: Annotated[
        str | None,
        typer.Option(
            "--plan",
            help=(
                "Restrict staging to sections already read in this reading plan, so "
                "the compiled wiki compounds with reading progress. Omit to stage "
                "every section of the document."
            ),
        ),
    ] = None,
    compile_wiki: Annotated[
        bool,
        typer.Option(
            "--compile",
            help="After staging, run 'llmwiki compile --root <workspace>/llmwiki' incrementally.",
        ),
    ] = False,
    print_command: Annotated[
        bool,
        typer.Option(
            "--print",
            help="With --compile, print the llmwiki compile command instead of running it.",
        ),
    ] = False,
) -> None:
    """Stage BookGraph sections into the workspace's llmwiki ``sources/`` project.

    Each section becomes its own bounded ``sources/<section_id>.md`` file carrying
    BookGraph provenance, so a large book is never routed through one truncating
    full-book ingest. Staging is idempotent: unchanged sections are left untouched
    so llmwiki's incremental compile adds only a daily batch without reprocessing
    the whole book. BookGraph's canonical inputs are only read, never mutated.
    """

    # --print only has meaning for the compile step; reject it early rather than
    # silently dropping the flag when the user forgot --compile.
    if print_command and not compile_wiki:
        raise typer.BadParameter("--print applies to the compile step; pass --compile as well.")

    paths = _resolve_workspace(workspace_path)
    resolved_doc_id = _validate_id(doc_id, "doc_id")

    manifest = paths.sources_sections / resolved_doc_id / "sections.jsonl"
    if not manifest.is_file():
        raise typer.BadParameter(
            f"Sections manifest not found: {manifest}. Run 'bookgraph segment' first."
        )
    try:
        sections = read_sections(manifest)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Invalid sections manifest: {manifest}: {exc}") from exc

    if plan_id is not None:
        resolved_plan_id = _validate_id(plan_id, "plan_id")
        plan_path = paths.reading_plans_root / f"{resolved_plan_id}.json"
        if not plan_path.is_file():
            raise typer.BadParameter(
                f"Reading plan not found: {plan_path}. "
                "Run 'bookgraph reading-plan create' first."
            )
        try:
            plan = read_reading_plan(plan_path)
        except (OSError, ValueError) as exc:
            raise typer.BadParameter(f"Invalid reading plan: {plan_path}: {exc}") from exc
        if plan.doc_id != resolved_doc_id:
            raise typer.BadParameter(
                f"Reading plan '{resolved_plan_id}' is for document '{plan.doc_id}', "
                f"not '{resolved_doc_id}'."
            )
        completed = set(plan.completed)
        # Preserve reading order (manifest order), staging only sections read so far.
        sections = [section for section in sections if section.id in completed]
        if not sections:
            typer.echo(
                f"No sections read yet in plan '{resolved_plan_id}'; nothing to stage."
            )
            return

    result = stage_sections(sections, paths.llmwiki_sources)
    typer.echo(f"doc_id: {resolved_doc_id}")
    typer.echo(f"sources: {result.sources_dir}")
    typer.echo(f"staged: {len(result.staged)}")
    typer.echo(f"unchanged: {len(result.unchanged)}")

    if not compile_wiki:
        return

    compile_command = ["llmwiki", "compile", "--root", str(paths.llmwiki_root)]
    if print_command:
        typer.echo(shlex.join(compile_command))
        return

    _run_llmwiki(compile_command)
