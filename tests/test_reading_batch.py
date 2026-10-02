from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from bookgraph.documents import write_document
from bookgraph.index.sqlite import SqliteIndexBackend
from bookgraph.mcp import service
from bookgraph.mcp.reading_batch import (
    BatchRequirements,
    ReadingBatchReport,
    complete_reading_batch,
    validate_reading_batch,
)
from bookgraph.mcp.service import ConceptInput, ReadingServiceError
from bookgraph.models import CanonicalBlock, Document, ReadingPlan, Section
from bookgraph.reading_plans import plan_lock, read_reading_plan, write_reading_plan
from bookgraph.sections import read_sections, write_sections
from bookgraph.workspace import WorkspacePaths

DOC = "deep-work"
A = "deep-work.a"
B = "deep-work.b"
C = "deep-work.c"

# Only the checks a test is about; the rest stay off so each test isolates one rule.
NOTHING = BatchRequirements(require_annotation=False, index="ignore", require_assets=False)


def _section(section_id: str, text: str = "Body.", block_ids: list[str] | None = None) -> Section:
    title = section_id.split(".")[-1].upper()
    return Section(
        id=section_id,
        doc_id=DOC,
        title=title,
        level=1,
        heading_path=[title],
        text=text,
        block_ids=block_ids or [],
    )


def _workspace(tmp_path: Path, *sections: Section, daily: int = 2) -> WorkspacePaths:
    workspace = WorkspacePaths(tmp_path)
    sections = sections or (_section(A), _section(B), _section(C))
    write_sections(list(sections), workspace.sources_sections / DOC)
    write_reading_plan(
        ReadingPlan(
            plan_id="daily",
            doc_id=DOC,
            daily_sections=daily,
            section_ids=[section.id for section in sections],
        ),
        workspace.reading_plans_root / "daily.json",
    )
    return workspace


def _plan(workspace: WorkspacePaths) -> ReadingPlan:
    return read_reading_plan(workspace.reading_plans_root / "daily.json")


def _build_index(workspace: WorkspacePaths) -> None:
    sections = read_sections(workspace.sources_sections / DOC / "sections.jsonl")
    SqliteIndexBackend().build_document(workspace, DOC, DOC, sections)


def _annotate(workspace: WorkspacePaths, section_id: str, *concepts: str) -> None:
    service.annotate_section(
        workspace,
        DOC,
        section_id,
        [ConceptInput(title=title) for title in concepts] if concepts else None,
        summary=f"Summary of {section_id}.",
    )


def _codes(report: ReadingBatchReport, *, blocking: bool | None = None) -> list[str]:
    return [issue.code for issue in report.issues if blocking is None or issue.blocking is blocking]


def test_complete_marks_whole_batch_when_annotated_and_indexed(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _annotate(workspace, A, "Deep Work")
    _annotate(workspace, B)  # summary-only annotation is still an annotation
    _build_index(workspace)

    report = complete_reading_batch(
        workspace, "daily", requirements=BatchRequirements(require_assets=False)
    )

    assert report.ok and report.committed
    assert report.section_ids == [A, B]
    assert report.issues == []
    assert (report.completed, report.total, report.done) == (2, 3, False)
    assert _plan(workspace).completed == [A, B]


def test_failed_checks_block_and_leave_the_plan_untouched(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _annotate(workspace, A)  # B is not annotated, nothing is indexed
    before = (workspace.reading_plans_root / "daily.json").read_text()

    report = complete_reading_batch(workspace, "daily")

    assert not report.ok and not report.committed
    assert _codes(report) == ["index_missing", "annotation_missing"]
    missing = next(issue for issue in report.issues if issue.code == "annotation_missing")
    assert missing.section_id == B and "annotate_section" in missing.message
    assert report.index_rebuild_needed is True
    assert report.completed == 0
    # All or nothing: A passed its own annotation check but is not marked either.
    assert (workspace.reading_plans_root / "daily.json").read_text() == before


def test_validate_reports_without_writing(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    before = (workspace.reading_plans_root / "daily.json").read_text()

    report = validate_reading_batch(workspace, "daily", requirements=NOTHING)

    assert report.ok and not report.committed
    assert (workspace.reading_plans_root / "daily.json").read_text() == before


def test_annotation_written_after_build_makes_index_stale(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _build_index(workspace)
    _annotate(workspace, A, "Deep Work")
    _annotate(workspace, B)
    reqs = BatchRequirements(require_assets=False)

    stale = validate_reading_batch(workspace, "daily", requirements=reqs)
    assert _codes(stale) == ["index_stale", "index_stale"]
    assert "bookgraph index build" in stale.issues[0].message

    _build_index(workspace)
    assert validate_reading_batch(workspace, "daily", requirements=reqs).ok

    _annotate(workspace, A, "Flow")  # re-annotating after the rebuild is stale again
    assert _codes(validate_reading_batch(workspace, "daily", requirements=reqs)) == ["index_stale"]


def test_hand_edited_concepts_are_detected_as_stale(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _annotate(workspace, A, "Deep Work")
    _annotate(workspace, B)
    _build_index(workspace)
    path = workspace.annotations_root / DOC / f"{A}.json"
    # Same summary/model/created_at, different concept set: only the edge check sees it.
    payload = json.loads(path.read_text())
    payload["concepts"] = [{"slug": "flow", "title": "Flow", "gloss": ""}]
    path.write_text(json.dumps(payload))

    report = validate_reading_batch(
        workspace, "daily", requirements=BatchRequirements(require_assets=False)
    )

    assert [(i.code, i.section_id) for i in report.issues] == [("index_stale", A)]


def test_deferred_index_reports_but_does_not_block(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _annotate(workspace, A)
    _annotate(workspace, B)

    report = complete_reading_batch(
        workspace,
        "daily",
        requirements=BatchRequirements(index="deferred", require_assets=False),
    )

    assert report.committed
    assert report.index_rebuild_needed is True
    assert _codes(report, blocking=False) == ["index_missing"]


def test_ignore_index_skips_the_index_entirely(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _annotate(workspace, A)
    _annotate(workspace, B)

    report = complete_reading_batch(
        workspace, "daily", requirements=BatchRequirements(index="ignore", require_assets=False)
    )

    assert report.committed and report.issues == []
    assert report.index_rebuild_needed is False


def test_unannotated_section_is_fresh_only_without_a_stored_annotation(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _annotate(workspace, A)
    _build_index(workspace)
    (workspace.annotations_root / DOC / f"{A}.json").unlink()  # index still has it
    reqs = BatchRequirements(require_annotation=False, require_assets=False)

    report = validate_reading_batch(workspace, "daily", requirements=reqs)

    assert [(i.code, i.section_id) for i in report.issues] == [("index_stale", A)]


def test_corrupt_or_misplaced_annotation_is_invalid(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _annotate(workspace, B)
    (workspace.annotations_root / DOC / f"{A}.json").write_text("{not json")
    misplaced = workspace.annotations_root / DOC / f"{B}.json"
    misplaced.write_text(misplaced.read_text().replace(B, C))
    reqs = BatchRequirements(index="ignore", require_assets=False)

    report = validate_reading_batch(workspace, "daily", requirements=reqs)

    assert [(i.code, i.section_id) for i in report.issues] == [
        ("annotation_invalid", A),
        ("annotation_invalid", B),
    ]
    assert not report.ok


def _figures_workspace(tmp_path: Path) -> WorkspacePaths:
    workspace = _workspace(tmp_path, _section(A, block_ids=["img1", "tbl1", "ghost"]), daily=1)
    image = workspace.sources_parsed / DOC / "images" / "fig1.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\xff\xd8\xff")
    (workspace.sources_parsed / DOC / "images" / "tbl1.jpg").write_bytes(b"\xff\xd8\xff")
    write_document(
        Document(
            doc_id=DOC,
            title="Deep Work",
            blocks=[
                CanonicalBlock(id="img1", type="image", text="Figure 1.", asset_path="fig1.jpg"),
                CanonicalBlock(id="tbl1", type="table", text="Table 1.", asset_path="tbl1.jpg"),
                CanonicalBlock(id="ghost", type="image", asset_path="never-staged.jpg"),
            ],
        ),
        workspace.sources_parsed / DOC,
    )
    return workspace


def test_assets_must_be_inspected(tmp_path: Path) -> None:
    workspace = _figures_workspace(tmp_path)
    reqs = BatchRequirements(require_annotation=False, index="ignore")

    report = complete_reading_batch(workspace, "daily", requirements=reqs)

    assert not report.committed
    assert _codes(report, blocking=True) == ["asset_not_inspected", "asset_not_inspected"]
    # A file the parser never staged cannot be inspected, so it only warns.
    assert _codes(report, blocking=False) == ["asset_file_missing"]
    assert "fig1.jpg" in report.issues[1].message

    inspected = reqs.model_copy(update={"inspected_assets": ["img1", "tbl1", "typo"]})
    report = complete_reading_batch(workspace, "daily", requirements=inspected)

    assert report.committed
    assert _codes(report) == ["asset_file_missing", "asset_unknown"]
    assert _plan(workspace).completed == [A]


def test_translation_presence(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section(A), daily=1)
    reqs = NOTHING.model_copy(update={"translation_lang": "VI"})
    target = workspace.root / "translations" / "vi" / DOC / f"{A}.md"

    assert _codes(validate_reading_batch(workspace, "daily", requirements=reqs)) == [
        "translation_missing"
    ]
    target.parent.mkdir(parents=True)
    target.write_text("")  # an empty body is a failed translation, not a cached one
    assert not validate_reading_batch(workspace, "daily", requirements=reqs).ok
    target.write_text("Bản dịch.")
    # A body with no registry sidecar may be reused, but its freshness is unknown.
    report = validate_reading_batch(workspace, "daily", requirements=reqs)
    assert report.ok
    assert _codes(report, blocking=False) == ["translation_untracked"]


def test_translation_registry_freshness(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section(A, text="Original."), daily=1)
    reqs = NOTHING.model_copy(update={"translation_lang": "vi"})
    service.write_section_translation(workspace, DOC, A, "vi", "Bản gốc.")

    assert validate_reading_batch(workspace, "daily", requirements=reqs).issues == []

    # The section changes after it was translated: the cached translation is stale.
    write_sections([_section(A, text="Revised.")], workspace.sources_sections / DOC)
    report = complete_reading_batch(workspace, "daily", requirements=reqs)

    assert not report.committed
    assert [(i.code, i.section_id) for i in report.issues] == [("translation_stale", A)]
    assert "write_section_translation" in report.issues[0].message


def test_artifact_templates(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section(A), daily=1)
    reqs = NOTHING.model_copy(update={"artifacts": ["notes/{plan_id}/{section_id}.md"]})

    report = validate_reading_batch(workspace, "daily", requirements=reqs)
    assert [(i.code, i.section_id) for i in report.issues] == [("artifact_missing", A)]
    assert f"notes/daily/{A}.md" in report.issues[0].message

    note = workspace.root / "notes" / "daily" / f"{A}.md"
    note.parent.mkdir(parents=True)
    note.write_text("note")
    assert validate_reading_batch(workspace, "daily", requirements=reqs).ok


@pytest.mark.parametrize(
    "template", ["../outside/{section_id}.md", "/etc/{doc_id}", "x/{nope}.md", "x/{0}.md", ""]
)
def test_invalid_artifact_template_is_a_request_error(tmp_path: Path, template: str) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(ReadingServiceError):
        validate_reading_batch(
            workspace, "daily", requirements=NOTHING.model_copy(update={"artifacts": [template]})
        )


def test_invalid_translation_lang_is_a_request_error(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(ReadingServiceError):
        validate_reading_batch(
            workspace, "daily", requirements=NOTHING.model_copy(update={"translation_lang": "../x"})
        )


def test_explicit_sections_unknown_ids_block_everything(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    report = complete_reading_batch(workspace, "daily", [C, "deep-work.zzz", C], NOTHING)

    assert report.section_ids == [C, "deep-work.zzz"]  # de-duplicated, caller order
    assert [(i.code, i.section_id) for i in report.issues] == [
        ("section_not_in_plan", "deep-work.zzz")
    ]
    assert _plan(workspace).completed == []


def test_section_dropped_by_resegment_is_reported(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    write_sections([_section(A), _section(B)], workspace.sources_sections / DOC)

    report = validate_reading_batch(workspace, "daily", [C], NOTHING)

    assert _codes(report) == ["section_missing"]


def test_already_read_is_idempotent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    complete_reading_batch(workspace, "daily", [A], NOTHING)

    report = complete_reading_batch(workspace, "daily", [A, B], NOTHING)

    assert report.committed
    assert _codes(report, blocking=False) == ["already_read"]
    assert _plan(workspace).completed == [A, B]


def test_request_errors(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section(A), daily=1)

    with pytest.raises(ReadingServiceError, match="must not be empty"):
        validate_reading_batch(workspace, "daily", [], NOTHING)
    with pytest.raises(ReadingServiceError):
        validate_reading_batch(workspace, "../escape", None, NOTHING)

    complete_reading_batch(workspace, "daily", requirements=NOTHING)
    with pytest.raises(ReadingServiceError, match="already complete"):
        complete_reading_batch(workspace, "daily", requirements=NOTHING)


@pytest.mark.parametrize("writer", ["mark_read", "complete_reading_batch"])
def test_concurrent_plan_writes_do_not_lose_updates(tmp_path: Path, writer: str) -> None:
    workspace = _workspace(tmp_path)
    path = workspace.reading_plans_root / "daily.json"

    def write_b() -> None:
        if writer == "mark_read":
            service.mark_read(workspace, "daily", B)
        else:
            complete_reading_batch(workspace, "daily", [B], NOTHING)

    with plan_lock(path):
        other = threading.Thread(target=write_b)
        other.start()
        other.join(timeout=0.2)
        assert other.is_alive()  # blocked on the lock, not racing our write
        # Another writer's read-modify-write lands while the thread waits ...
        write_reading_plan(_plan(workspace).model_copy(update={"completed": [A]}), path)
    other.join(timeout=5)

    # ... and the waiting writer applies its mark on top of it instead of clobbering it.
    assert _plan(workspace).completed == [A, B]


def test_plan_writes_leave_no_temp_files(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    complete_reading_batch(workspace, "daily", requirements=NOTHING)
    service.mark_read(workspace, "daily")

    names = sorted(p.name for p in workspace.reading_plans_root.iterdir())
    assert not [name for name in names if name.endswith(".tmp")]
    assert "daily.json" in names
    assert _plan(workspace).completed == [A, B, C]


# front matter → Chapter 1 (two subsections) → Chapter 2
def _chaptered_workspace(tmp_path: Path) -> WorkspacePaths:
    def node(section_id: str, level: int) -> Section:
        return _section(section_id).model_copy(update={"level": level})

    return _workspace(
        tmp_path,
        node("deep-work.ch1", 1),
        node("deep-work.ch1-a", 2),
        node("deep-work.ch2", 1),
        node("deep-work.ch2-a", 2),
        daily=3,
    )


def test_default_batch_matches_a_boundary_clipped_get_next_section(tmp_path: Path) -> None:
    workspace = _chaptered_workspace(tmp_path)
    clipped = service.get_next_section(workspace, "daily", stop_at_boundary=True)
    assert [view.id for view in clipped.sections] == ["deep-work.ch1", "deep-work.ch1-a"]

    # Relaxed checks: nothing but the batch selection stands between the call and the
    # write, so the batch must be the one the agent was actually handed.
    report = complete_reading_batch(workspace, "daily", requirements=NOTHING, stop_at_boundary=True)

    assert report.committed
    assert report.section_ids == [view.id for view in clipped.sections]
    assert _plan(workspace).completed == ["deep-work.ch1", "deep-work.ch1-a"]
    assert "deep-work.ch2" not in _plan(workspace).completed  # past the chapter boundary


def test_default_batch_without_boundary_matches_get_next_section(tmp_path: Path) -> None:
    workspace = _chaptered_workspace(tmp_path)
    unclipped = service.get_next_section(workspace, "daily")

    report = validate_reading_batch(workspace, "daily", requirements=NOTHING)

    assert report.section_ids == [view.id for view in unclipped.sections]
    assert len(report.section_ids) == 3  # spills into Chapter 2, as get_next_section does


def test_chapter_level_scopes_the_boundary(tmp_path: Path) -> None:
    workspace = _chaptered_workspace(tmp_path)
    complete_reading_batch(workspace, "daily", ["deep-work.ch1"], NOTHING)

    report = validate_reading_batch(
        workspace, "daily", requirements=NOTHING, stop_at_boundary=True, chapter_level=2
    )

    assert report.section_ids == ["deep-work.ch1-a"]
    with pytest.raises(ReadingServiceError, match="chapter_level"):
        validate_reading_batch(workspace, "daily", requirements=NOTHING, chapter_level=0)


def test_explicit_section_ids_take_precedence_over_the_boundary(tmp_path: Path) -> None:
    workspace = _chaptered_workspace(tmp_path)

    report = validate_reading_batch(
        workspace, "daily", ["deep-work.ch2"], NOTHING, stop_at_boundary=True
    )

    assert report.section_ids == ["deep-work.ch2"]
