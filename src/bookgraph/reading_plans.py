from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bookgraph.models import ReadingPlan, Section
from bookgraph.utils import validate_slug_id


@dataclass(frozen=True)
class ContextPack:
    """The next reading tick: the unread sections a reader should tackle next."""

    plan_id: str
    doc_id: str
    sections: list[str]
    remaining: int
    done: bool


def create_reading_plan(
    sections: list[Section],
    *,
    plan_id: str,
    doc_id: str,
    daily_sections: int = 1,
) -> ReadingPlan:
    """Build a fresh reading plan from a document's ordered sections.

    ``sections`` must be in reading order (``read_sections`` preserves the
    manifest's line order). ``plan_id`` doubles as the persisted filename, so it
    is validated as a filesystem-safe slug up front rather than trusting the
    caller. An empty document has nothing to plan and is rejected.
    """

    validate_slug_id(plan_id, field_name="plan_id")
    validate_slug_id(doc_id, field_name="doc_id")
    if daily_sections < 1:
        raise ValueError("daily_sections must be at least 1")
    if not sections:
        raise ValueError("cannot create a reading plan from an empty sections manifest")

    return ReadingPlan(
        plan_id=plan_id,
        doc_id=doc_id,
        daily_sections=daily_sections,
        section_ids=[section.id for section in sections],
        completed=[],
    )


def next_sections(plan: ReadingPlan) -> ContextPack:
    """Peek the next ``daily_sections`` unread sections without mutating state."""

    completed = set(plan.completed)
    unread = [section_id for section_id in plan.section_ids if section_id not in completed]
    batch = unread[: plan.daily_sections]
    return ContextPack(
        plan_id=plan.plan_id,
        doc_id=plan.doc_id,
        sections=batch,
        remaining=len(unread),
        done=not unread,
    )


def mark_section_read(plan: ReadingPlan, section_id: str | None = None) -> tuple[ReadingPlan, str]:
    """Return a copy of ``plan`` with ``section_id`` marked read.

    When ``section_id`` is omitted the next unread section is marked. Marking an
    already-read section is idempotent. Returns the updated plan and the id that
    was marked so callers can report it.
    """

    known = set(plan.section_ids)
    completed = set(plan.completed)

    if section_id is None:
        pack = next_sections(plan)
        if pack.done:
            raise ValueError(f"reading plan '{plan.plan_id}' is already complete")
        section_id = pack.sections[0]
    elif section_id not in known:
        raise ValueError(f"section '{section_id}' is not in reading plan '{plan.plan_id}'")

    if section_id in completed:
        return plan, section_id

    updated = plan.model_copy(update={"completed": [*plan.completed, section_id]})
    return updated, section_id


def write_reading_plan(plan: ReadingPlan, path: Path) -> Path:
    """Persist a reading plan to ``reading_plans/<plan_id>.json``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(indent=2) + "\n")
    return path


def read_reading_plan(path: Path) -> ReadingPlan:
    """Load a reading plan written by :func:`write_reading_plan`."""

    return ReadingPlan.model_validate_json(path.read_text())


@dataclass(frozen=True)
class PlanProgress:
    """A reading plan's completion progress, for listings."""

    plan_id: str
    doc_id: str
    completed: int
    total: int
    done: bool


def list_plan_progress(root: Path) -> list[PlanProgress]:
    """Progress for every readable plan under ``root``, sorted by file name.

    Unreadable/corrupt plans are skipped rather than failing the whole listing.
    Shared by the CLI ``reading-plan list`` and the MCP ``list_plans`` tool so a
    future change (sort order, new corruption case) applies to both.
    """

    progress: list[PlanProgress] = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            plan = read_reading_plan(path)
        except (OSError, ValueError):
            continue
        total = len(plan.section_ids)
        completed = len(plan.completed)
        progress.append(
            PlanProgress(
                plan_id=plan.plan_id,
                doc_id=plan.doc_id,
                completed=completed,
                total=total,
                done=total > 0 and completed >= total,
            )
        )
    return progress
