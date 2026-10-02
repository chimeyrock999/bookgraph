from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from bookgraph.models import ReadingPlan, Section
from bookgraph.reading_plans import (
    ContextPack,
    create_reading_plan,
    mark_section_read,
    next_sections,
    plan_lock,
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


def _a_plan() -> ReadingPlan:
    return ReadingPlan(plan_id="daily", doc_id="doc", section_ids=["doc.a", "doc.b"])


def test_write_reading_plan_keeps_original_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "reading_plans" / "daily.json"
    write_reading_plan(_a_plan(), path)
    before = path.read_text()

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    updated = _a_plan().model_copy(update={"completed": ["doc.a"]})
    with pytest.raises(OSError, match="disk full"):
        write_reading_plan(updated, path)

    assert path.read_text() == before
    assert [p.name for p in path.parent.iterdir()] == ["daily.json"]  # temp file removed


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_write_reading_plan_uses_umask_mode_and_keeps_existing_mode(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    previous = os.umask(0o022)
    try:
        write_reading_plan(_a_plan(), path)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644  # not mkstemp's owner-only 0600

    path.chmod(0o640)
    write_reading_plan(_a_plan(), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


_HOLD_LOCK = """
import sys
from pathlib import Path
from bookgraph.reading_plans import plan_lock

with plan_lock(Path(sys.argv[1])):
    print("locked", flush=True)
    sys.stdin.readline()
"""


@pytest.mark.skipif(os.name != "posix", reason="flock is POSIX-only")
def test_plan_lock_blocks_writers_in_another_process(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"
        acquired = threading.Event()

        def take_lock() -> None:
            with plan_lock(path):
                acquired.set()

        waiter = threading.Thread(target=take_lock)
        waiter.start()
        assert not acquired.wait(0.3)  # the other process's flock holds us off

        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert acquired.wait(5)
        waiter.join(5)
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="POSIX permissions, non-root"
)
def test_plan_lock_works_with_a_read_only_lock_file(tmp_path: Path) -> None:
    path = tmp_path / "daily.json"
    lock = tmp_path / ".daily.json.lock"
    lock.touch()
    lock.chmod(0o444)  # e.g. created by another user under umask 022

    with plan_lock(path):
        write_reading_plan(_a_plan(), path)

    assert read_reading_plan(path) == _a_plan()


@pytest.mark.skipif(os.name != "posix", reason="flock is POSIX-only")
def test_plan_lock_prefers_a_writable_lock_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An exclusive flock on NFS needs a descriptor open for writing, so the read-only
    # open must stay a fallback for lock files we cannot write.
    flags: list[int] = []
    real_open = os.open

    def recording_open(file: object, flag: int, mode: int = 0o777) -> int:
        if str(file).endswith(".lock"):
            flags.append(flag)
        return real_open(file, flag, mode)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", recording_open)
    with plan_lock(tmp_path / "daily.json"):
        pass

    assert len(flags) == 1 and flags[0] & os.O_ACCMODE == os.O_RDWR
