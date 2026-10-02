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
    chapter_progress,
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


def _node(section_id: str, level: int) -> Section:
    return Section(
        id=section_id,
        doc_id="iceberg",
        title=section_id.title(),
        level=level,
        heading_path=[section_id],
        text="Body.",
    )


# front matter (level 1) → Chapter 1 (with nested subsections) → Chapter 2
_BOOK = [
    _node("preface", 1),
    _node("ch1", 1),
    _node("ch1-a", 2),
    _node("ch1-a-i", 3),
    _node("ch1-b", 2),
    _node("ch2", 1),
    _node("ch2-a", 2),
]


def _book_plan(*, completed: list[str] | None = None, daily_sections: int = 2) -> ReadingPlan:
    return ReadingPlan(
        plan_id="iceberg",
        doc_id="iceberg",
        daily_sections=daily_sections,
        section_ids=[section.id for section in _BOOK],
        completed=completed or [],
    )


def test_chapter_progress_counts_within_the_current_chapter() -> None:
    plan = _book_plan(completed=["preface", "ch1", "ch1-a"])

    progress = chapter_progress(plan, _BOOK)

    assert progress.chapter_id == "ch1"
    assert progress.chapter_title == "Ch1"
    assert progress.current_section_id == "ch1-a-i"
    assert progress.completed_in_chapter == 2
    assert progress.remaining_in_chapter == 2
    assert progress.total_in_chapter == 4
    assert progress.next_section_ids == ["ch1-a-i", "ch1-b"]
    assert progress.next_boundary_id == "ch2"
    assert progress.remaining == 4
    assert not progress.done


def test_chapter_progress_clips_next_sections_at_the_boundary() -> None:
    plan = _book_plan(completed=["preface", "ch1", "ch1-a", "ch1-a-i"], daily_sections=3)

    progress = chapter_progress(plan, _BOOK)

    # ch1-b is the last unread section of Chapter 1; ch2 lies past the boundary
    assert progress.next_section_ids == ["ch1-b"]
    assert progress.remaining_in_chapter == 1
    assert progress.next_boundary_id == "ch2"


def test_chapter_progress_works_on_a_fresh_or_reset_plan() -> None:
    progress = chapter_progress(_book_plan(), _BOOK)

    # front matter is its own top-level unit until it is read or skipped
    assert progress.chapter_id == "preface"
    assert progress.total_in_chapter == 1
    assert progress.next_section_ids == ["preface"]
    assert progress.next_boundary_id == "ch1"


def test_chapter_progress_ignores_out_of_order_reads_elsewhere() -> None:
    # front matter skipped by marking it read, and a Chapter 2 section read early
    plan = _book_plan(completed=["ch2-a", "preface"])

    progress = chapter_progress(plan, _BOOK)

    assert progress.chapter_id == "ch1"
    assert progress.completed_in_chapter == 0
    assert progress.remaining_in_chapter == 4
    assert progress.remaining == 5


def test_chapter_progress_counts_out_of_order_reads_inside_the_chapter() -> None:
    plan = _book_plan(completed=["preface", "ch1-b"])

    progress = chapter_progress(plan, _BOOK)

    assert progress.chapter_id == "ch1"
    assert progress.completed_in_chapter == 1
    assert progress.remaining_in_chapter == 3
    assert progress.next_section_ids == ["ch1", "ch1-a"]


def test_chapter_progress_scopes_to_a_requested_heading_level() -> None:
    plan = _book_plan(completed=["preface", "ch1"])

    progress = chapter_progress(plan, _BOOK, chapter_level=2)

    assert progress.chapter_id == "ch1-a"
    assert progress.chapter_level == 2
    assert progress.total_in_chapter == 2
    assert progress.next_boundary_id == "ch1-b"


def test_chapter_progress_uses_the_current_section_when_shallower_than_level() -> None:
    progress = chapter_progress(_book_plan(completed=["preface"]), _BOOK, chapter_level=2)

    # the current section (ch1, level 1) is above the requested level: it is the scope
    assert progress.chapter_id == "ch1"


def test_chapter_progress_last_chapter_has_no_boundary() -> None:
    plan = _book_plan(completed=["preface", "ch1", "ch1-a", "ch1-a-i", "ch1-b"])

    progress = chapter_progress(plan, _BOOK)

    assert progress.chapter_id == "ch2"
    assert progress.next_boundary_id is None


def test_chapter_progress_only_counts_sections_in_the_plan() -> None:
    plan = _book_plan().model_copy(
        update={"section_ids": ["ch1", "ch1-a", "ch1-b", "ch2"]}  # ch1-a-i not planned
    )

    progress = chapter_progress(plan, _BOOK)

    assert progress.total_in_chapter == 3


def test_chapter_progress_reports_done_plan_without_a_chapter() -> None:
    plan = _book_plan(completed=[section.id for section in _BOOK])

    progress = chapter_progress(plan, _BOOK)

    assert progress.done
    assert progress.chapter_id is None
    assert progress.next_section_ids == []
    assert progress.remaining_in_chapter == 0


def test_chapter_progress_rejects_a_plan_section_missing_from_the_document() -> None:
    plan = _book_plan().model_copy(update={"section_ids": ["ghost", "ch1"]})

    with pytest.raises(ValueError, match="ghost"):
        chapter_progress(plan, _BOOK)


def test_chapter_progress_rejects_a_non_positive_chapter_level() -> None:
    with pytest.raises(ValueError, match="chapter_level"):
        chapter_progress(_book_plan(), _BOOK, chapter_level=0)


# a single "# Book" root above "##" chapters (common for Markdown/EPUB ingestion)
_ROOTED_BOOK = [
    _node("book", 1),
    _node("ch1", 2),
    _node("ch1-a", 3),
    _node("ch2", 2),
]


def _rooted_plan(*, completed: list[str] | None = None) -> ReadingPlan:
    return ReadingPlan(
        plan_id="iceberg",
        doc_id="iceberg",
        daily_sections=5,
        section_ids=[section.id for section in _ROOTED_BOOK],
        completed=completed or [],
    )


def test_chapter_progress_skips_a_lone_book_root() -> None:
    progress = chapter_progress(_rooted_plan(completed=["book", "ch1"]), _ROOTED_BOOK)

    assert progress.chapter_id == "ch1"
    assert progress.remaining_in_chapter == 1
    assert progress.next_boundary_id == "ch2"
    assert progress.next_section_ids == ["ch1-a"]


def test_chapter_progress_scopes_a_lone_root_being_read_to_itself() -> None:
    # day 1 of a fresh plan on a book-rooted doc: the root's own text is the scope,
    # so a boundary-clipped batch stops before the first chapter
    progress = chapter_progress(_rooted_plan(), _ROOTED_BOOK)

    assert progress.chapter_id == "book"
    assert progress.total_in_chapter == 1
    assert progress.remaining_in_chapter == 1
    assert progress.next_section_ids == ["book"]
    assert progress.next_boundary_id == "ch1"
    assert progress.remaining == 4


def test_chapter_progress_explicit_level_does_not_skip_the_root() -> None:
    progress = chapter_progress(
        _rooted_plan(completed=["book", "ch1"]), _ROOTED_BOOK, chapter_level=1
    )

    assert progress.chapter_id == "book"
    assert progress.next_boundary_id is None


# parts above chapters: read with chapter_level=2
_PARTS_BOOK = [
    _node("p1", 1),
    _node("ch1", 2),
    _node("ch1-a", 3),
    _node("ch2", 2),
    _node("p2", 1),
    _node("ch3", 2),
]


def _parts_plan(*, completed: list[str] | None = None) -> ReadingPlan:
    return ReadingPlan(
        plan_id="iceberg",
        doc_id="iceberg",
        daily_sections=5,
        section_ids=[section.id for section in _PARTS_BOOK],
        completed=completed or [],
    )


def test_chapter_progress_scopes_a_part_heading_being_read_to_itself() -> None:
    progress = chapter_progress(_parts_plan(), _PARTS_BOOK, chapter_level=2)

    assert progress.chapter_id == "p1"
    assert progress.next_section_ids == ["p1"]
    assert progress.next_boundary_id == "ch1"


def test_chapter_progress_part_heading_mid_book_does_not_spill() -> None:
    plan = _parts_plan(completed=["p1", "ch1", "ch1-a", "ch2"])

    progress = chapter_progress(plan, _PARTS_BOOK, chapter_level=2)

    assert progress.next_section_ids == ["p2"]
    assert progress.next_boundary_id == "ch3"


def test_chapter_progress_explicit_level_on_a_rooted_book_day_one() -> None:
    progress = chapter_progress(_rooted_plan(), _ROOTED_BOOK, chapter_level=2)

    assert progress.next_section_ids == ["book"]
    assert progress.next_boundary_id == "ch1"


def test_chapter_progress_level_jump_keeps_the_part_subtree() -> None:
    sections = [_node("p1", 1), _node("sec", 3), _node("p2", 1)]
    plan = ReadingPlan(
        plan_id="iceberg",
        doc_id="iceberg",
        daily_sections=5,
        section_ids=["p1", "sec", "p2"],
        completed=["p1"],
    )

    progress = chapter_progress(plan, sections, chapter_level=2)

    # sec has no level-2 ancestor-or-self, so it is scoped to p1's subtree
    assert progress.chapter_id == "p1"
    assert progress.next_section_ids == ["sec"]
    assert progress.next_boundary_id == "p2"
