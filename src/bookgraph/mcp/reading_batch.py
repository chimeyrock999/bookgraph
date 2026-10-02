"""A formal completion boundary for a reading batch.

A reading job does more than read: per section it may translate, inspect figures and
tables, annotate, rebuild the index, and only then mark progress. ``mark_read`` alone
leaves that ordering to prompt discipline — an agent whose annotation write or index
rebuild failed half-way can still advance the plan. This module makes the boundary
explicit:

- :func:`validate_reading_batch` checks a batch's enrichment readiness and reports
  every problem as a structured, actionable :class:`BatchIssue`, without writing.
- :func:`complete_reading_batch` runs the same checks and marks the whole batch read
  in **one** atomic plan write only when no blocking issue remains — all or nothing.

Which checks apply is declared per call by :class:`BatchRequirements`, so a light
"just reading" session and a strict translate-annotate-index job share one tool. See
``docs/cli/commands.md`` (``bookgraph mcp``) for the contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from bookgraph.annotations import annotation_path, read_annotation
from bookgraph.index import default_index_backend
from bookgraph.mcp.service import (
    ReadingServiceError,
    _current_batch,
    _load_doc_blocks,
    _load_doc_sections,
    _load_plan,
    _plan_path,
    _section_assets,
    translation_structure_issues,
)
from bookgraph.models import ReadingPlan, Section, SectionAnnotation
from bookgraph.reading_plans import (
    mark_section_read,
    plan_lock,
    write_reading_plan,
)
from bookgraph.translation_structure import describe_structure_issues
from bookgraph.translations import section_content_hash, translation_state, validate_lang
from bookgraph.workspace import WorkspacePaths

IndexPolicy = Literal["fresh", "deferred", "ignore"]

_ARTIFACT_FIELDS = ("doc_id", "section_id", "plan_id")


class BatchRequirements(BaseModel):
    """What must be true of every section in a batch before it may be marked read.

    - ``require_annotation``: the section has a readable Tier-2 annotation file.
    - ``index``: ``"fresh"`` — the document is indexed and the index reflects the
      section's current annotation (blocking); ``"deferred"`` — the same check, but a
      missing/stale index is reported without blocking (a later ``index build``, e.g.
      the nightly maintenance pass, will fold it in); ``"ignore"`` — skip it.
    - ``require_assets``: every figure/table of the section that resolves to a file is
      listed in ``inspected_assets`` (block ids the caller has opened/embedded).
    - ``translation_lang``: when set, the section's cached translation
      (``translations/<lang>/<doc_id>/<section_id>.md``) exists, is non-empty, and is
      not ``stale`` in the translation registry (an ``untracked`` body only warns), and
      keeps the section's link destinations, image paths, reference definitions, HTML
      anchors, and heading ids unchanged.
    - ``artifacts``: extra workspace-relative path templates that must exist and be
      non-empty per section; ``{doc_id}``, ``{section_id}`` and ``{plan_id}`` expand.
    """

    require_annotation: bool = True
    index: IndexPolicy = "fresh"
    require_assets: bool = True
    inspected_assets: list[str] = Field(default_factory=list)
    translation_lang: str | None = None
    artifacts: list[str] = Field(default_factory=list)


class BatchIssue(BaseModel):
    """One readiness problem. ``blocking`` issues prevent the batch from completing.

    ``code`` is stable and machine-readable; ``message`` says what to do about it.
    ``section_id`` is ``None`` for a batch-wide issue (e.g. the document is unindexed).
    """

    code: str
    message: str
    section_id: str | None = None
    blocking: bool = True


class ReadingBatchReport(BaseModel):
    """The outcome of validating — and, for ``complete_reading_batch``, committing — a batch.

    ``ok`` is true when no blocking issue was found. ``committed`` is true only when
    ``complete_reading_batch`` actually marked the batch read; on ``ok=False`` the plan
    is untouched. ``index_rebuild_needed`` flags a missing/stale index whatever the
    policy, so a ``"deferred"`` caller knows to schedule ``bookgraph index build``.
    ``completed`` / ``total`` / ``done`` describe the plan after this call.
    """

    plan_id: str
    doc_id: str
    section_ids: list[str]
    ok: bool
    committed: bool = False
    index_rebuild_needed: bool = False
    issues: list[BatchIssue] = Field(default_factory=list)
    completed: int
    total: int
    done: bool


def validate_reading_batch(
    workspace: WorkspacePaths,
    plan_id: str,
    section_ids: list[str] | None = None,
    requirements: BatchRequirements | None = None,
    *,
    stop_at_boundary: bool = False,
    chapter_level: int | None = None,
) -> ReadingBatchReport:
    """Check a batch's readiness without writing anything.

    ``section_ids`` defaults to the plan's current batch — exactly what
    ``get_next_section`` returns for the same ``stop_at_boundary`` / ``chapter_level``,
    so an agent reading boundary-clipped batches must pass the same flags here. Explicit
    ``section_ids`` take precedence over both flags.

    Request errors — an unknown plan, an invalid ``translation_lang``, artifact template
    or ``chapter_level``, an empty batch — raise :class:`ReadingServiceError`; readiness
    problems are returned as issues.
    """

    return _evaluate(
        workspace, plan_id, section_ids, requirements, stop_at_boundary, chapter_level
    )[2]


def complete_reading_batch(
    workspace: WorkspacePaths,
    plan_id: str,
    section_ids: list[str] | None = None,
    requirements: BatchRequirements | None = None,
    *,
    stop_at_boundary: bool = False,
    chapter_level: int | None = None,
) -> ReadingBatchReport:
    """Validate a batch and, only if nothing blocks, mark all of it read atomically.

    The whole batch is marked in a single plan write (temp file + rename), so progress
    either advances by the entire batch or not at all. Already-read sections are
    idempotent. On any blocking issue the plan is left unchanged and the report lists
    every reason, so the caller can fix them all before retrying.
    """

    # Checks and write run under one plan lock, so a concurrent mark_read cannot land
    # between the plan load inside _evaluate and the replace below and be overwritten.
    with plan_lock(_plan_path(workspace, plan_id)):
        path, plan, report = _evaluate(
            workspace, plan_id, section_ids, requirements, stop_at_boundary, chapter_level
        )
        if not report.ok:
            return report
        updated = plan
        for section_id in report.section_ids:
            updated, _ = mark_section_read(updated, section_id)
        if updated is not plan:
            write_reading_plan(updated, path)
    completed, total, done = _progress(updated)
    return report.model_copy(
        update={"committed": True, "completed": completed, "total": total, "done": done}
    )


def _progress(plan: ReadingPlan) -> tuple[int, int, bool]:
    completed = len(set(plan.completed))
    total = len(plan.section_ids)
    return completed, total, total > 0 and completed >= total


def _resolve_batch(
    plan: ReadingPlan,
    sections: list[Section],
    section_ids: list[str] | None,
    stop_at_boundary: bool,
    chapter_level: int | None,
) -> list[str]:
    if chapter_level is not None and chapter_level < 1:
        raise ReadingServiceError("chapter_level must be at least 1")
    if section_ids is None:
        if not set(plan.section_ids) - set(plan.completed):
            raise ReadingServiceError(f"reading plan '{plan.plan_id}' is already complete")
        return _current_batch(
            plan, sections, stop_at_boundary=stop_at_boundary, chapter_level=chapter_level
        )
    if not section_ids:
        raise ReadingServiceError("section_ids must not be empty (omit it for the current batch)")
    return list(dict.fromkeys(section_ids))  # de-duplicate, keep the caller's order


def _evaluate(
    workspace: WorkspacePaths,
    plan_id: str,
    section_ids: list[str] | None,
    requirements: BatchRequirements | None,
    stop_at_boundary: bool = False,
    chapter_level: int | None = None,
) -> tuple[Path, ReadingPlan, ReadingBatchReport]:
    reqs = requirements or BatchRequirements()
    path, plan = _load_plan(workspace, plan_id)
    lang = _validate_lang(reqs.translation_lang)
    templates = [_validate_template(template) for template in reqs.artifacts]
    sections = _load_doc_sections(workspace, plan.doc_id)
    batch = _resolve_batch(plan, sections, section_ids, stop_at_boundary, chapter_level)

    sections_by_id = {section.id: section for section in sections}
    in_plan = set(plan.section_ids)
    completed = set(plan.completed)
    issues: list[BatchIssue] = []
    checkable: list[Section] = []
    for section_id in batch:
        if section_id not in in_plan:
            issues.append(
                BatchIssue(
                    code="section_not_in_plan",
                    section_id=section_id,
                    message=f"Section '{section_id}' is not in reading plan '{plan.plan_id}'.",
                )
            )
            continue
        section = sections_by_id.get(section_id)
        if section is None:
            issues.append(
                BatchIssue(
                    code="section_missing",
                    section_id=section_id,
                    message=(
                        f"Section '{section_id}' is in the plan but no longer in "
                        f"'{plan.doc_id}' sections.jsonl (re-segmented?); recreate the plan."
                    ),
                )
            )
            continue
        if section_id in completed:
            issues.append(
                BatchIssue(
                    code="already_read",
                    section_id=section_id,
                    blocking=False,
                    message=f"Section '{section_id}' is already marked read.",
                )
            )
        checkable.append(section)

    index_rebuild_needed = False
    index_blocks = reqs.index == "fresh"
    rebuild_hint = f"Run 'bookgraph index build <workspace> {plan.doc_id}'."
    backend = default_index_backend()
    indexed = reqs.index != "ignore" and plan.doc_id in backend.indexed_doc_ids(workspace)
    if reqs.index != "ignore" and not indexed:
        index_rebuild_needed = True
        issues.append(
            BatchIssue(
                code="index_missing",
                blocking=index_blocks,
                message=f"Document '{plan.doc_id}' is not indexed. {rebuild_hint}",
            )
        )

    inspected = set(reqs.inspected_assets)
    check_assets = reqs.require_assets or bool(inspected)
    blocks_by_id = _load_doc_blocks(workspace, plan.doc_id) if check_assets else {}
    seen_assets: set[str] = set()

    for section in checkable:
        annotation = _check_annotation(workspace, section, reqs.require_annotation, issues)

        if indexed and not _index_reflects(workspace, section, annotation):
            index_rebuild_needed = True
            issues.append(
                BatchIssue(
                    code="index_stale",
                    section_id=section.id,
                    blocking=index_blocks,
                    message=(
                        f"The index does not reflect the current annotation of "
                        f"'{section.id}'. {rebuild_hint}"
                    ),
                )
            )

        if check_assets:
            assets, summaries = _section_assets(workspace, section, blocks_by_id)
            seen_assets.update(summary.block_id for summary in summaries)
            for summary in summaries:
                if not summary.resolved:
                    issues.append(
                        BatchIssue(
                            code="asset_file_missing",
                            section_id=section.id,
                            blocking=False,
                            message=(
                                f"Asset '{summary.block_id}' has no file on disk, so it "
                                "cannot be inspected; not required."
                            ),
                        )
                    )
            if reqs.require_assets:
                for asset in assets:
                    if asset.block_id not in inspected:
                        issues.append(
                            BatchIssue(
                                code="asset_not_inspected",
                                section_id=section.id,
                                message=(
                                    f"{asset.type.capitalize()} '{asset.block_id}' "
                                    f"({asset.path}) was not inspected; open it and list "
                                    "its block id in inspected_assets."
                                ),
                            )
                        )

        if lang is not None:
            issues.extend(_translation_issues(workspace, lang, section))

        for template in templates:
            relative_path = _render_template(template, plan.plan_id, section)
            if not _non_empty_file(_inside_workspace(workspace, relative_path)):
                issues.append(
                    BatchIssue(
                        code="artifact_missing",
                        section_id=section.id,
                        message=f"Required artifact '{relative_path}' is missing or empty.",
                    )
                )

    for block_id in sorted(inspected - seen_assets):
        issues.append(
            BatchIssue(
                code="asset_unknown",
                blocking=False,
                message=(
                    f"inspected_assets lists '{block_id}', which is not an asset of this batch."
                ),
            )
        )

    completed_count, total, done = _progress(plan)
    report = ReadingBatchReport(
        plan_id=plan.plan_id,
        doc_id=plan.doc_id,
        section_ids=batch,
        ok=not any(issue.blocking for issue in issues),
        index_rebuild_needed=index_rebuild_needed,
        issues=issues,
        completed=completed_count,
        total=total,
        done=done,
    )
    return path, plan, report


def _check_annotation(
    workspace: WorkspacePaths, section: Section, required: bool, issues: list[BatchIssue]
) -> SectionAnnotation | None:
    """The section's valid annotation (or ``None``), recording any problem as an issue.

    Validity matches what ``index build`` accepts: a readable file whose payload names
    this document and section. A misplaced or corrupt file is ignored by the build, so
    it must not count as "annotated" here either.
    """

    path = annotation_path(workspace.annotations_root, section.doc_id, section.id)
    if not path.is_file():
        if required:
            issues.append(
                BatchIssue(
                    code="annotation_missing",
                    section_id=section.id,
                    message=f"No annotation for '{section.id}'; call annotate_section first.",
                )
            )
        return None
    try:
        annotation = read_annotation(path)
    except (OSError, ValueError) as exc:
        reason = f"is unreadable ({exc.__class__.__name__})"
    else:
        if annotation.doc_id == section.doc_id and annotation.section_id == section.id:
            return annotation
        reason = "names a different document/section"
    issues.append(
        BatchIssue(
            code="annotation_invalid",
            section_id=section.id,
            blocking=required,
            message=f"Annotation {path} {reason}; rewrite it with annotate_section.",
        )
    )
    return None


def _index_reflects(
    workspace: WorkspacePaths, section: Section, annotation: SectionAnnotation | None
) -> bool:
    """Whether the index was built from the section's current annotation state.

    Compares the stored annotation provenance (summary/model/created_at) with the
    file, and — when the annotation asserts concepts — the section's indexed concept
    edges with the annotation's (all agent-sourced, same slugs). An unannotated
    section is fresh when the index stores no annotation for it either.
    """

    backend = default_index_backend()
    stored = backend.indexed_annotation(workspace, section.doc_id, section.id)
    if annotation is None:
        return stored is None
    if stored is None or (stored.summary, stored.model, stored.created_at) != (
        annotation.summary,
        annotation.model,
        annotation.created_at,
    ):
        return False
    if annotation.concepts is None:
        return True
    indexed = backend.section_concepts(workspace, section.doc_id, section.id)
    if any(node.source != "agent" for node in indexed):
        return False
    return {node.slug for node in indexed} == {concept.slug for concept in annotation.concepts}


def _translation_issues(workspace: WorkspacePaths, lang: str, section: Section) -> list[BatchIssue]:
    """Check the section's cached translation against the translation registry.

    ``missing`` (or an empty body) and ``stale`` (the section changed since it was
    translated) block; ``untracked`` — a body with no valid registry sidecar, e.g.
    written by hand or before the registry existed — only warns, since its freshness is
    unknown rather than known-bad. A usable (fresh or untracked) body that dropped,
    added, or rewrote a structural target (a link destination, image path, reference
    definition, HTML anchor, heading id) blocks: the intra-book links it breaks are
    known-bad.
    """

    state = translation_state(
        workspace, lang, section.doc_id, section.id, section_content_hash(section)
    )
    relative = state.paths.body.relative_to(workspace.root)
    if state.status == "missing" or not state.body:
        return [
            BatchIssue(
                code="translation_missing",
                section_id=section.id,
                message=f"Translation '{relative}' is missing or empty; write it first.",
            )
        ]
    if state.status == "stale":
        return [
            BatchIssue(
                code="translation_stale",
                section_id=section.id,
                message=(
                    f"Translation '{relative}' was made from an older version of the "
                    "section; re-translate it with write_section_translation."
                ),
            )
        ]
    issues: list[BatchIssue] = []
    if state.status == "untracked":
        issues.append(
            BatchIssue(
                code="translation_untracked",
                section_id=section.id,
                blocking=False,
                message=(
                    f"Translation '{relative}' has no registry record, so its freshness is "
                    "unknown; write it with write_section_translation to track it."
                ),
            )
        )
    structure = translation_structure_issues(workspace, state, section)
    if structure:
        issues.append(
            BatchIssue(
                code="translation_structure_changed",
                section_id=section.id,
                message=(
                    f"Translation '{relative}' changed structural Markdown "
                    f"({describe_structure_issues(structure)}); translate prose and link "
                    "labels only, keep every link destination, path, anchor, and heading "
                    "id byte-for-byte, and rewrite it with write_section_translation."
                ),
            )
        )
    return issues


def _validate_lang(lang: str | None) -> str | None:
    if lang is None:
        return None
    try:
        return validate_lang(lang)
    except ValueError as exc:
        raise ReadingServiceError(f"invalid translation_lang: {exc}") from exc


def _validate_template(template: str) -> str:
    """Reject an artifact template that is absolute, escapes, or has unknown fields."""

    sample = Section(id="doc.section", doc_id="doc", title="", level=1, heading_path=[], text="")
    rendered = _render_template(template, "plan", sample)
    candidate = Path(rendered)
    if not rendered.strip() or candidate.is_absolute() or ".." in candidate.parts:
        raise ReadingServiceError(
            f"artifact template '{template}' must be a workspace-relative path"
        )
    return template


def _render_template(template: str, plan_id: str, section: Section) -> str:
    values = {"doc_id": section.doc_id, "section_id": section.id, "plan_id": plan_id}
    try:
        return template.format_map(values)
    except (KeyError, IndexError, ValueError) as exc:
        raise ReadingServiceError(
            f"artifact template '{template}' is invalid ({exc!s}); allowed fields: "
            + ", ".join("{" + field + "}" for field in _ARTIFACT_FIELDS)
        ) from exc


def _inside_workspace(workspace: WorkspacePaths, relative_path: str) -> Path:
    """Resolve a rendered artifact path, refusing anything outside the workspace."""

    root = workspace.root.resolve()
    resolved = (root / relative_path).resolve()
    if not resolved.is_relative_to(root):
        raise ReadingServiceError(f"artifact path '{relative_path}' escapes the workspace")
    return resolved


def _non_empty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
