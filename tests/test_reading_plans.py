from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.models import ReadingPlan, Section
from bookgraph.reading_plans import (
    ContextPack,
    create_reading_plan,
    mark_section_read,
    next_sections,
    read_reading_plan,
    write_reading_plan,
)


def _section(section_id: str) -> Section:
    return Section(
        id=section_id,
        doc_id="deep-work",
        title=section_id,
        level=1,
        heading_path=[section_id],
        text="Body.",
    )


def _plan(
    *section_ids: str,
    daily_sections: int = 1,
    completed: list[str] | None = None,
) -> ReadingPlan:
    return ReadingPlan(
        plan_id="deep-work",
        doc_id="deep-work",
        daily_sections=daily_sections,
        section_ids=list(section_ids),
        completed=completed or [],
    )


def test_create_reading_plan_preserves_reading_order() -> None:
    sections = [_section("deep-work.a"), _section("deep-work.b"), _section("deep-work.c")]

    plan = create_reading_plan(sections, plan_id="daily", doc_id="deep-work", daily_sections=2)

    assert plan.section_ids == ["deep-work.a", "deep-work.b", "deep-work.c"]
    assert plan.completed == []
    assert plan.daily_sections == 2


def test_create_reading_plan_rejects_empty_manifest() -> None:
    with pytest.raises(ValueError, match="empty sections manifest"):
        create_reading_plan([], plan_id="daily", doc_id="deep-work")


@pytest.mark.parametrize("bad_id", ["../escape", "Daily", "a/b"])
def test_create_reading_plan_rejects_unsafe_plan_id(bad_id: str) -> None:
    with pytest.raises(ValueError, match="plan_id"):
        create_reading_plan([_section("deep-work.a")], plan_id=bad_id, doc_id="deep-work")


def test_create_reading_plan_rejects_non_positive_daily_sections() -> None:
    with pytest.raises(ValueError, match="daily_sections must be at least 1"):
        create_reading_plan(
            [_section("deep-work.a")], plan_id="daily", doc_id="deep-work", daily_sections=0
        )


def test_next_sections_returns_only_unread_up_to_daily_limit() -> None:
    plan = _plan("a", "b", "c", "d", daily_sections=2, completed=["a"])

    pack = next_sections(plan)

    assert pack == ContextPack(
        plan_id="deep-work", doc_id="deep-work", sections=["b", "c"], remaining=3, done=False
    )


def test_next_sections_reports_done_when_all_read() -> None:
    plan = _plan("a", "b", completed=["a", "b"])

    pack = next_sections(plan)

    assert pack.sections == []
    assert pack.remaining == 0
    assert pack.done is True


def test_mark_section_read_defaults_to_next_unread() -> None:
    plan = _plan("a", "b", completed=["a"])

    updated, marked = mark_section_read(plan)

    assert marked == "b"
    assert updated.completed == ["a", "b"]
    # The original plan is not mutated.
    assert plan.completed == ["a"]


def test_mark_section_read_is_idempotent_for_already_read_sections() -> None:
    plan = _plan("a", "b", completed=["a"])

    updated, marked = mark_section_read(plan, "a")

    assert marked == "a"
    assert updated.completed == ["a"]


def test_mark_section_read_rejects_unknown_section() -> None:
    plan = _plan("a", "b")

    with pytest.raises(ValueError, match="not in reading plan"):
        mark_section_read(plan, "ghost")


def test_mark_section_read_raises_when_plan_complete_and_no_id_given() -> None:
    plan = _plan("a", completed=["a"])

    with pytest.raises(ValueError, match="already complete"):
        mark_section_read(plan)


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    plan = _plan("a", "b", daily_sections=2, completed=["a"])
    path = tmp_path / "reading_plans" / "deep-work.json"

    written = write_reading_plan(plan, path)

    assert written == path
    assert read_reading_plan(path) == plan
