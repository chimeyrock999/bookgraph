from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated

import typer

from bookgraph.cli._app import index_app
from bookgraph.cli._shared import _validate_id
from bookgraph.documents import read_document
from bookgraph.index import Concept, IndexUnavailableError, default_index_backend
from bookgraph.sections import read_sections
from bookgraph.utils import ID_PATTERN
from bookgraph.workspace import WorkspacePaths


def _segmented_doc_ids(workspace: WorkspacePaths) -> list[str]:
    root = workspace.sources_sections
    return sorted(
        child.name
        for child in (root.iterdir() if root.is_dir() else [])
        if (child / "sections.jsonl").is_file() and ID_PATTERN.fullmatch(child.name)
    )


def _doc_title(workspace: WorkspacePaths, doc_id: str) -> str:
    """The parsed document title when available, else the doc id."""

    parsed = workspace.sources_parsed / doc_id / "document.json"
    if parsed.is_file():
        try:
            return read_document(parsed).title
        except (OSError, ValueError):
            pass
    return doc_id


@index_app.command("build")
def index_build(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
    doc_id: Annotated[
        str | None,
        typer.Option(
            "--doc-id",
            help="Index only this document. Defaults to every segmented document.",
        ),
    ] = None,
) -> None:
    """Build the SQLite index (search + graph) under indexes/ from sections."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    if doc_id is not None:
        doc_ids = [_validate_id(doc_id, "doc_id")]
    else:
        doc_ids = _segmented_doc_ids(workspace)
        if not doc_ids:
            raise typer.BadParameter(
                f"No segmented documents under {workspace.sources_sections}. "
                "Run 'bookgraph segment' first."
            )

    backend = default_index_backend()
    indexed_any = False
    try:
        for current_doc in doc_ids:
            manifest = workspace.sources_sections / current_doc / "sections.jsonl"
            if not manifest.is_file():
                raise typer.BadParameter(
                    f"Sections manifest not found: {manifest}. Run 'bookgraph segment' first."
                )
            try:
                sections = read_sections(manifest)
            except (OSError, ValueError) as exc:
                raise typer.BadParameter(f"Invalid sections manifest: {manifest}: {exc}") from exc

            title = _doc_title(workspace, current_doc)
            count = backend.build_document(workspace, current_doc, title, sections)
            indexed_any = True
            typer.echo(f"doc_id: {current_doc}")
            typer.echo(f"sections: {count}")
    except IndexUnavailableError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        # Report where the db is even if a later document aborts the run, so a
        # partial build (earlier docs already written) still tells the caller the
        # index location instead of silently swallowing it.
        if indexed_any:
            typer.echo(f"backend: {backend.name}")
            typer.echo(f"index: {backend.location(workspace)}")


@index_app.command("concepts")
def index_concepts(
    workspace_path: Annotated[Path, typer.Argument(help="BookGraph workspace/output root path.")],
) -> None:
    """Render cross-book concept pages under wiki/concepts/ from the index."""

    workspace = WorkspacePaths(workspace_path.expanduser().resolve())
    backend = default_index_backend()
    concepts = backend.concepts(workspace)
    if not concepts:
        raise typer.BadParameter(
            f"No concepts in {backend.location(workspace)}. Run 'bookgraph index build' first."
        )

    concepts_dir = workspace.wiki_concepts
    if concepts_dir.exists():
        shutil.rmtree(concepts_dir)
    concepts_dir.mkdir(parents=True, exist_ok=True)

    title_cache: dict[str, str] = {}
    written = 0
    missing_links = 0
    for concept in concepts:
        (concepts_dir / f"{concept.node.slug}.md").write_text(
            _render_concept_page(workspace, concept, title_cache)
        )
        written += 1
        for mention in concept.mentions:
            book_page = (
                workspace.wiki_books / mention.doc_id / "sections" / f"{mention.section_id}.md"
            )
            if not book_page.is_file():
                missing_links += 1

    typer.echo(f"concepts: {written}")
    typer.echo(f"wiki: {concepts_dir}")
    if missing_links:
        # Backlinks resolve into wiki/books/, which is the wiki backend's surface
        # (never written here). Flag it rather than emit silently dead links.
        typer.echo(
            f"warning: {missing_links} concept backlink(s) point to book pages not yet "
            "compiled; run 'bookgraph wiki compile <doc_id> --backend markdown-graph' "
            "for each book to materialize them."
        )


def _render_concept_page(
    workspace: WorkspacePaths, concept: Concept, title_cache: dict[str, str]
) -> str:
    node = concept.node
    lines = [
        f"# {node.title}",
        "",
        f"Mentioned in {node.doc_count} "
        f"{'book' if node.doc_count == 1 else 'books'} · "
        f"{node.mention_count} {'section' if node.mention_count == 1 else 'sections'}.",
    ]
    current_doc: str | None = None
    for mention in concept.mentions:  # already ordered by doc, then reading order
        if mention.doc_id != current_doc:
            current_doc = mention.doc_id
            if mention.doc_id not in title_cache:
                title_cache[mention.doc_id] = _doc_title(workspace, mention.doc_id)
            lines += ["", f"## {title_cache[mention.doc_id]}", ""]
        link = f"../books/{mention.doc_id}/sections/{mention.section_id}.md"
        # A Tier-2 backlink carries the agent's per-mention gloss and an
        # (agent-verified) marker; a plain Tier-1 backlink is just the section link.
        suffix = ""
        if mention.gloss:
            suffix += f" — {mention.gloss}"
        if mention.source == "agent":
            suffix += " (agent-verified)"
        lines.append(f"- [{mention.title}]({link}){suffix}")
        # The section's Tier-2 summary is the long-form context behind the backlink;
        # render it as an indented blockquote so the page reads as a concept note, not
        # only a list of glosses. Each summary stays under its own section link, keeping
        # the note provenance-aware.
        if mention.summary:
            for para in mention.summary.split("\n"):
                lines.append(f"  > {para}" if para else "  >")
    return "\n".join(lines) + "\n"
