"""Reading-plan tools: next section, progress, ``get_section``, ``mark_read``, and
workspace orientation (re-exported by :mod:`bookgraph.mcp.service`)."""

from __future__ import annotations

import json

from bookgraph.mcp.errors import (
    ReadingServiceError,
    SectionNotFoundError,
)
from bookgraph.mcp.loading import (
    _chapter_progress,
    _chapter_view,
    _current_batch,
    _find_section,
    _load_doc_blocks,
    _load_doc_sections,
    _load_plan,
    _plan_path,
    _section_ref,
    _section_view,
    _segmented_doc_ids,
    _validate_id,
)
from bookgraph.mcp.views import (
    CreatedPlan,
    DocumentList,
    DocumentRef,
    MarkReadResult,
    NextSection,
    PlanList,
    PlanProgress,
    PlanRef,
    SectionView,
)
from bookgraph.reading_plans import (
    create_reading_plan,
    list_plan_progress,
    mark_section_read,
    next_sections,
    plan_lock,
    write_reading_plan,
)
from bookgraph.sections import count_sections
from bookgraph.workspace import WorkspacePaths


def get_next_section(
    workspace: WorkspacePaths,
    plan_id: str,
    include_assets: bool = True,
    *,
    stop_at_boundary: bool = False,
    chapter_level: int | None = None,
) -> NextSection:
    """Return the next unread sections for a plan, with full content.

    ``include_assets`` (default true) mirrors ``get_section`` — set it false to skip the
    figure/table resolution (and its ``document.json`` read) on plans read for prose only.
    The result carries ``chapter`` progress (see :func:`get_plan_progress`); with
    ``stop_at_boundary`` the batch is clipped at the end of that chapter, so a tick never
    spills into the next one. ``chapter_level`` picks the heading level of the chapter
    scope (default: the top-level ancestor, skipping a lone book-title root).
    """

    _, plan = _load_plan(workspace, plan_id)
    pack = next_sections(plan)
    sections = _load_doc_sections(workspace, plan.doc_id)
    by_id = {section.id: section for section in sections}
    for section_id in pack.sections:
        if section_id not in by_id:
            raise SectionNotFoundError(
                f"Reading plan '{plan_id}' references unknown section '{section_id}' "
                f"in document '{plan.doc_id}'."
            )
    progress = _chapter_progress(plan, sections, chapter_level)
    batch = _current_batch(plan, sections, stop_at_boundary=stop_at_boundary, progress=progress)
    blocks_by_id = _load_doc_blocks(workspace, plan.doc_id) if include_assets else None
    views = [
        _section_view(
            workspace, by_id[section_id], include_assets=include_assets, blocks_by_id=blocks_by_id
        )
        for section_id in batch
    ]
    return NextSection(
        plan_id=plan.plan_id,
        doc_id=plan.doc_id,
        sections=views,
        remaining=pack.remaining,
        done=pack.done,
        chapter=_chapter_view(progress, by_id),
    )


def get_plan_progress(
    workspace: WorkspacePaths, plan_id: str, chapter_level: int | None = None
) -> PlanProgress:
    """Report a plan's progress overall and within its current chapter.

    Answers "how many sections until the end of this chapter" without fetching section
    bodies or the full outline: the chapter is the scope ancestor of the next unread
    section (resolved by :func:`~bookgraph.graph.resolve_chapter`; ``chapter_level`` picks
    its heading level), counts are by membership so resets and skipped/out-of-order reads stay
    correct, and ``next_sections`` is the next ``daily_sections`` batch clipped at the
    chapter boundary.
    """

    _, plan = _load_plan(workspace, plan_id)
    sections = _load_doc_sections(workspace, plan.doc_id)
    by_id = {section.id: section for section in sections}
    progress = _chapter_progress(plan, sections, chapter_level)
    return PlanProgress(
        plan_id=plan.plan_id,
        doc_id=plan.doc_id,
        completed=len(plan.section_ids) - progress.remaining,
        total=len(plan.section_ids),
        remaining=progress.remaining,
        done=progress.done,
        current_section_id=progress.current_section_id,
        chapter=_chapter_view(progress, by_id),
        next_sections=[_section_ref(by_id[section_id]) for section_id in progress.next_section_ids],
    )


def get_section(
    workspace: WorkspacePaths,
    doc_id: str,
    section_id: str,
    include_assets: bool = True,
    include_blocks: bool = False,
) -> SectionView:
    """Return one section's full reading content by id.

    When ``include_assets`` (the default) the view carries a structured ``assets`` list
    of the section's figures/tables (path, type, caption, order) so a reader no longer has
    to grep the parsed ``document.json`` to find them. ``include_blocks`` adds the
    section's parsed source blocks (id, type, text), which a block-aligned translation
    references.
    """

    section = _find_section(workspace, doc_id, section_id)
    return _section_view(
        workspace, section, include_assets=include_assets, include_blocks=include_blocks
    )


def mark_read(
    workspace: WorkspacePaths, plan_id: str, section_id: str | None = None
) -> MarkReadResult:
    """Mark a section read for a plan and persist the updated plan."""

    path = _plan_path(workspace, plan_id)
    with plan_lock(path):
        _, plan = _load_plan(workspace, plan_id)
        try:
            updated, marked = mark_section_read(plan, section_id)
        except ValueError as exc:
            raise ReadingServiceError(str(exc)) from exc
        write_reading_plan(updated, path)
    return MarkReadResult(
        plan_id=updated.plan_id,
        marked=marked,
        completed=len(updated.completed),
        total=len(updated.section_ids),
        done=len(updated.completed) == len(updated.section_ids),
    )


# --- Workspace orientation & reading-plan management -----------------------------
#
# Self-serve tools so a reading agent connected over MCP can discover what there is
# to read (``list_documents``) and start/track a reading plan (``create_plan`` /
# ``list_plans``) without a human running the CLI first. They reuse the same pure
# reading-plan logic as ``cli/reading_plan.py``.


def _document_title(workspace: WorkspacePaths, doc_id: str) -> str:
    """The parsed document's title, falling back to ``doc_id``."""

    path = workspace.sources_parsed / doc_id / "document.json"
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return doc_id
    title = payload.get("title") if isinstance(payload, dict) else None
    return title if isinstance(title, str) and title else doc_id


def list_documents(workspace: WorkspacePaths) -> DocumentList:
    """List the workspace's segmented documents (what an agent can read)."""

    documents: list[DocumentRef] = []
    for doc_id in _segmented_doc_ids(workspace):
        manifest = workspace.sources_sections / doc_id / "sections.jsonl"
        try:
            section_count = count_sections(manifest)
        except OSError:
            continue  # a manifest that vanished/broke between listing and read
        documents.append(
            DocumentRef(
                doc_id=doc_id,
                title=_document_title(workspace, doc_id),
                section_count=section_count,
            )
        )
    return DocumentList(documents=documents)


def create_plan(
    workspace: WorkspacePaths,
    doc_id: str,
    plan_id: str | None = None,
    daily_sections: int = 1,
    *,
    overwrite: bool = False,
) -> CreatedPlan:
    """Create (and persist) a reading plan for a segmented document.

    ``plan_id`` defaults to ``doc_id``. To protect the autonomous agents this
    tool serves, creating a plan whose id already exists raises rather than
    silently discarding its progress; pass ``overwrite=True`` to replace it.
    """

    resolved_doc_id = _validate_id(doc_id, "doc_id")
    resolved_plan_id = _validate_id(plan_id or resolved_doc_id, "plan_id")
    if daily_sections < 1:
        raise ReadingServiceError("daily_sections must be at least 1")

    path = workspace.reading_plans_root / f"{resolved_plan_id}.json"
    with plan_lock(path):
        if path.exists() and not overwrite:
            raise ReadingServiceError(
                f"reading plan '{resolved_plan_id}' already exists; resume it with "
                "get_next_section/list_plans, or pass overwrite=True to replace it "
                "(discarding its progress)"
            )

        sections = _load_doc_sections(workspace, resolved_doc_id)
        try:
            plan = create_reading_plan(
                sections,
                plan_id=resolved_plan_id,
                doc_id=resolved_doc_id,
                daily_sections=daily_sections,
            )
        except ValueError as exc:
            raise ReadingServiceError(str(exc)) from exc

        write_reading_plan(plan, path)
    return CreatedPlan(
        plan_id=plan.plan_id,
        doc_id=plan.doc_id,
        daily_sections=plan.daily_sections,
        section_count=len(plan.section_ids),
        path=str(path),
    )


def list_plans(workspace: WorkspacePaths) -> PlanList:
    """List the workspace's reading plans with completion progress."""

    return PlanList(
        plans=[
            PlanRef(
                plan_id=progress.plan_id,
                doc_id=progress.doc_id,
                completed=progress.completed,
                total=progress.total,
                done=progress.done,
            )
            for progress in list_plan_progress(workspace.reading_plans_root)
        ]
    )
