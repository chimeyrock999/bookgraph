"""``bookgraph concepts``: curate the concept registry and report concept hygiene.

Report commands (``suggest`` / ``lint`` / ``review``) read the built index and
``concepts/registry.json``. Write commands (``alias`` / ``unalias`` / ``canonical`` /
``ignore`` / ``distinct``) edit only the registry; the concept graph picks the change up
on the next ``bookgraph index build``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import concepts_app
from bookgraph.concept_hygiene import (
    DEFAULT_MERGE_THRESHOLD,
    lint_concepts,
    review_queue,
    suggest_merges,
)
from bookgraph.concept_registry import (
    ConceptRegistry,
    add_alias,
    ignore,
    mark_distinct,
    read_registry,
    remove_alias,
    set_canonical,
    title_from_slug,
    write_registry,
)
from bookgraph.index import Concept, default_index_backend
from bookgraph.workspace import WorkspacePaths

WorkspaceArg = Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")]
LimitOpt = Annotated[int, typer.Option("--limit", min=1, help="Maximum items to print.")]


def _workspace(path: Path) -> WorkspacePaths:
    return WorkspacePaths(path.expanduser().resolve())


def _registry(workspace: WorkspacePaths) -> ConceptRegistry:
    try:
        return read_registry(workspace.concept_registry)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _indexed_concepts(workspace: WorkspacePaths) -> list[Concept]:
    backend = default_index_backend()
    concepts = backend.concepts(workspace)
    if not concepts:
        raise typer.BadParameter(
            f"No concepts in {backend.location(workspace)}. Run 'bookgraph index build' first."
        )
    return concepts


def _indexed_title(workspace: WorkspacePaths, slug: str) -> str:
    """The concept's indexed title, else a title derived from its slug."""

    concept = default_index_backend().get_concept(workspace, slug)
    return concept.node.title if concept is not None else title_from_slug(slug)


def _update(
    workspace: WorkspacePaths, change: Callable[[ConceptRegistry], ConceptRegistry]
) -> None:
    try:
        registry = change(_registry(workspace))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    path = write_registry(registry, workspace.concept_registry)
    typer.echo(f"registry: {path}")
    typer.echo("next: run 'bookgraph index build' to apply it to the concept graph")


@concepts_app.command("suggest")
def concepts_suggest(
    workspace_path: WorkspaceArg,
    threshold: Annotated[
        float,
        typer.Option("--threshold", min=0.0, max=1.0, help="Minimum similarity score."),
    ] = DEFAULT_MERGE_THRESHOLD,
    limit: LimitOpt = 50,
) -> None:
    """Suggest likely-duplicate concepts to merge (alias -> canonical)."""

    workspace = _workspace(workspace_path)
    nodes = [concept.node for concept in _indexed_concepts(workspace)]
    suggestions = suggest_merges(nodes, _registry(workspace), threshold=threshold, limit=limit)
    for item in suggestions:
        typer.echo(f"{item.score:.2f}  {item.alias} -> {item.canonical}  ({item.reason})")
    typer.echo(f"suggestions: {len(suggestions)}")
    if suggestions:
        typer.echo("apply: bookgraph concepts alias <workspace> <alias> <canonical>")


@concepts_app.command("lint")
def concepts_lint(workspace_path: WorkspaceArg, limit: LimitOpt = 200) -> None:
    """Flag generic, one-off, over-granular, or stale concepts."""

    workspace = _workspace(workspace_path)
    findings = lint_concepts(_indexed_concepts(workspace), _registry(workspace))
    for finding in findings[:limit]:
        typer.echo(f"{finding.severity}  {finding.slug}  [{finding.rule}] {finding.message}")
    warnings = sum(1 for finding in findings if finding.severity == "warning")
    typer.echo(f"findings: {len(findings)} ({warnings} warning)")


@concepts_app.command("review")
def concepts_review(workspace_path: WorkspaceArg, limit: LimitOpt = 50) -> None:
    """List agent-created concepts not yet canonical, aliased, or ignored."""

    workspace = _workspace(workspace_path)
    items = review_queue(_indexed_concepts(workspace), _registry(workspace))
    for item in items[:limit]:
        gloss = f" — {item.gloss}" if item.gloss else ""
        typer.echo(
            f'{item.slug}  "{item.title}"  {item.doc_count} book(s), '
            f"{item.mention_count} section(s){gloss}"
        )
    typer.echo(f"pending: {len(items)}")
    if items:
        typer.echo("decide: bookgraph concepts canonical|alias|ignore <workspace> <slug> ...")


@concepts_app.command("alias")
def concepts_alias(
    workspace_path: WorkspaceArg,
    alias: Annotated[str, typer.Argument(help="Deprecated slug to fold into the canonical.")],
    canonical: Annotated[str, typer.Argument(help="Canonical concept slug.")],
    title: Annotated[
        str | None,
        typer.Option("--title", help="Canonical title (if the canonical is new to the registry)."),
    ] = None,
) -> None:
    """Make ALIAS resolve to CANONICAL (merge the two concepts)."""

    workspace = _workspace(workspace_path)
    resolved_title = title or _indexed_title(workspace, canonical)
    _update(workspace, lambda registry: add_alias(registry, alias, canonical, resolved_title))
    typer.echo(f"alias: {alias} -> {canonical}")


@concepts_app.command("unalias")
def concepts_unalias(
    workspace_path: WorkspaceArg,
    alias: Annotated[str, typer.Argument(help="Alias slug to detach.")],
) -> None:
    """Detach ALIAS from its canonical concept."""

    workspace = _workspace(workspace_path)
    _update(workspace, lambda registry: remove_alias(registry, alias))
    typer.echo(f"unaliased: {alias}")


@concepts_app.command("canonical")
def concepts_canonical(
    workspace_path: WorkspaceArg,
    slug: Annotated[str, typer.Argument(help="Concept slug to mark canonical.")],
    title: Annotated[str | None, typer.Option("--title", help="Canonical display title.")] = None,
    note: Annotated[str | None, typer.Option("--note", help="Reviewer note.")] = None,
) -> None:
    """Mark SLUG as a reviewed canonical concept (accepts it from the review queue)."""

    workspace = _workspace(workspace_path)
    resolved_title = title or _indexed_title(workspace, slug)
    _update(workspace, lambda registry: set_canonical(registry, slug, resolved_title, note))
    typer.echo(f'canonical: {slug} "{resolved_title}"')


@concepts_app.command("ignore")
def concepts_ignore(
    workspace_path: WorkspaceArg,
    slug: Annotated[str, typer.Argument(help="Concept slug never to index as a concept.")],
) -> None:
    """Never turn SLUG into a concept edge (drops it from every book on rebuild)."""

    workspace = _workspace(workspace_path)
    _update(workspace, lambda registry: ignore(registry, slug))
    typer.echo(f"ignored: {slug}")


@concepts_app.command("distinct")
def concepts_distinct(
    workspace_path: WorkspaceArg,
    left: Annotated[str, typer.Argument(help="First concept slug.")],
    right: Annotated[str, typer.Argument(help="Second concept slug.")],
) -> None:
    """Record that LEFT and RIGHT are different concepts (silences merge suggestions)."""

    workspace = _workspace(workspace_path)
    _update(workspace, lambda registry: mark_distinct(registry, left, right))
    typer.echo(f"distinct: {left} / {right}")
