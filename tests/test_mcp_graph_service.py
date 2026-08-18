from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.annotations import read_annotation
from bookgraph.index.sqlite import SqliteIndexBackend, db_path
from bookgraph.mcp import service
from bookgraph.mcp.service import (
    ConceptNotFoundError,
    InvalidIdError,
    ReadingServiceError,
    SectionNotFoundError,
    SectionsNotFoundError,
)
from bookgraph.models import Section
from bookgraph.sections import read_sections, write_sections
from bookgraph.workspace import WorkspacePaths


def _section(
    section_id: str,
    title: str,
    level: int,
    *,
    prev_id: str | None = None,
    next_id: str | None = None,
    text: str = "Body.",
) -> Section:
    return Section(
        id=section_id,
        doc_id="ddia",
        title=title,
        level=level,
        heading_path=[title],
        text=text,
        prev_id=prev_id,
        next_id=next_id,
    )


def _sections() -> list[Section]:
    # Part I > (Chapter 1, Chapter 2), Part II
    return [
        _section("ddia.part-1", "Part I", 1, next_id="ddia.ch-1"),
        _section(
            "ddia.ch-1",
            "Chapter 1",
            2,
            prev_id="ddia.part-1",
            next_id="ddia.ch-2",
            text="storage",
        ),
        _section("ddia.ch-2", "Chapter 2", 2, prev_id="ddia.ch-1", next_id="ddia.part-2"),
        _section("ddia.part-2", "Part II", 1, prev_id="ddia.ch-2"),
    ]


def _workspace(tmp_path: Path, *, build_index: bool = False) -> WorkspacePaths:
    workspace = WorkspacePaths(tmp_path)
    sections = _sections()
    write_sections(sections, workspace.sources_sections / "ddia")
    if build_index:
        SqliteIndexBackend().build_document(workspace, "ddia", "DDIA", sections)
    return workspace


def test_get_outline_returns_hierarchy_in_reading_order(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    outline = service.get_outline(workspace, "ddia")

    assert outline.doc_id == "ddia"
    assert [node.id for node in outline.nodes] == [
        "ddia.part-1",
        "ddia.ch-1",
        "ddia.ch-2",
        "ddia.part-2",
    ]
    by_id = {node.id: node for node in outline.nodes}
    assert by_id["ddia.part-1"].child_ids == ["ddia.ch-1", "ddia.ch-2"]
    assert by_id["ddia.ch-1"].parent_id == "ddia.part-1"
    assert by_id["ddia.part-2"].parent_id is None


def test_get_related_returns_parent_prev_next_and_children(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    related = service.get_related(workspace, "ddia", "ddia.ch-1")

    assert related.parent is not None and related.parent.id == "ddia.part-1"
    assert related.prev is not None and related.prev.id == "ddia.part-1"
    assert related.next is not None and related.next.id == "ddia.ch-2"
    assert related.children == []

    part = service.get_related(workspace, "ddia", "ddia.part-1")
    assert [child.id for child in part.children] == ["ddia.ch-1", "ddia.ch-2"]
    assert part.parent is None
    assert part.prev is None


def test_get_context_combines_full_content_with_neighbourhood(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    context = service.get_context(workspace, "ddia", "ddia.ch-1")

    assert context.section.id == "ddia.ch-1"
    assert context.section.text == "storage"
    assert context.related.parent is not None
    assert context.related.parent.id == "ddia.part-1"


def test_graph_tools_work_without_a_built_index(tmp_path: Path) -> None:
    # No `index build` run: the graph is rebuilt from sections on demand.
    workspace = _workspace(tmp_path, build_index=False)
    assert not db_path(workspace).exists()

    outline = service.get_outline(workspace, "ddia")
    assert len(outline.nodes) == 4


def test_graph_tools_prefer_the_persisted_index_when_present(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, build_index=True)
    assert db_path(workspace).is_file()

    outline = service.get_outline(workspace, "ddia")
    assert [node.id for node in outline.nodes] == [n.id for n in read_sections(
        workspace.sources_sections / "ddia" / "sections.jsonl"
    )]


def test_graph_tools_rebuild_from_sections_for_a_document_not_in_the_index(tmp_path: Path) -> None:
    """A document absent from the catalog is unindexed: rebuild from its sections."""

    workspace = _workspace(tmp_path)
    # Build a *different* document into the index so the database exists but does
    # not contain 'ddia'.
    SqliteIndexBackend().build_document(
        workspace, "other", "Other", [_section("other.x", "X", 1)]
    )
    assert db_path(workspace).is_file()

    outline = service.get_outline(workspace, "ddia")

    # 'ddia' is served from its sections manifest, not the index.
    assert [node.id for node in outline.nodes] == [
        "ddia.part-1",
        "ddia.ch-1",
        "ddia.ch-2",
        "ddia.part-2",
    ]


def test_get_related_raises_for_unknown_section(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(SectionNotFoundError, match="not found"):
        service.get_related(workspace, "ddia", "ddia.ghost")


def test_graph_tools_raise_for_unsegmented_document(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)

    with pytest.raises(SectionsNotFoundError, match="No sections"):
        service.get_outline(workspace, "ddia")


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "..", "UP"])
def test_graph_tools_reject_non_slug_doc_ids(tmp_path: Path, bad_id: str) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(InvalidIdError):
        service.get_outline(workspace, bad_id)
    with pytest.raises(InvalidIdError):
        service.get_related(workspace, bad_id, "ddia.ch-1")
    with pytest.raises(InvalidIdError):
        service.get_context(workspace, bad_id, "ddia.ch-1")


def test_get_concept_returns_cross_book_backlinks(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace,
        "ddia",
        "DDIA",
        [Section(id="ddia.a", doc_id="ddia", title="Schema Evolution", level=1,
                 heading_path=["Schema Evolution"], text="x")],
    )
    backend.build_document(
        workspace,
        "deep-work",
        "Deep Work",
        [Section(id="deep-work.a", doc_id="deep-work", title="Schema Evolution", level=1,
                 heading_path=["Schema Evolution"], text="x")],
    )

    concept = service.get_concept(workspace, "schema-evolution")

    assert concept.slug == "schema-evolution"
    assert concept.doc_count == 2
    assert concept.mention_count == 2
    assert sorted(m.doc_id for m in concept.mentions) == ["ddia", "deep-work"]


def test_get_concept_detail_view_surfaces_section_annotation_summaries(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    sections = [
        Section(id="ddia.a", doc_id="ddia", title="Schema Evolution", level=1,
                heading_path=["Schema Evolution"], text="x"),
    ]
    write_sections(sections, workspace.sources_sections / "ddia")
    backend = SqliteIndexBackend()
    backend.build_document(workspace, "ddia", "DDIA", sections)
    # Annotate, then rebuild so the summary lands in the index the concept reads.
    service.annotate_section(workspace, "ddia", "ddia.a", summary="how schemas evolve")
    backend.build_document(workspace, "ddia", "DDIA", sections)

    # Compact card (default): backlinks stay lightweight — no summary text — but the
    # card still counts the annotated mentions so it can signal deeper context exists.
    card = service.get_concept(workspace, "schema-evolution")
    assert card.annotated_mention_count == 1
    assert all(m.summary == "" for m in card.mentions)

    # Detail view: each mention carries its section's summary, tied to that section.
    detail = service.get_concept(workspace, "schema-evolution", include_annotations=True)
    assert detail.annotated_mention_count == 1
    assert {(m.section_id, m.summary) for m in detail.mentions} == {
        ("ddia.a", "how schemas evolve")
    }


def test_get_concept_raises_for_unknown_slug(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, build_index=True)

    with pytest.raises(ConceptNotFoundError):
        service.get_concept(workspace, "not-a-concept")


def test_get_concept_rejects_invalid_slug(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, build_index=True)

    with pytest.raises(InvalidIdError):
        service.get_concept(workspace, "../escape")


def test_get_context_includes_the_sections_concepts(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    sections = [
        Section(id="ddia.a", doc_id="ddia", title="Schema Evolution", level=1,
                heading_path=["Schema Evolution"], text="x")
    ]
    write_sections(sections, workspace.sources_sections / "ddia")
    SqliteIndexBackend().build_document(workspace, "ddia", "DDIA", sections)

    context = service.get_context(workspace, "ddia", "ddia.a")

    node = next(c for c in context.concepts if c.slug == "schema-evolution")
    assert node.title == "Schema Evolution"
    assert node.doc_count == 1
    assert node.mention_count == 1


def test_get_context_has_no_concepts_without_an_index(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, build_index=False)

    context = service.get_context(workspace, "ddia", "ddia.ch-1")

    assert context.concepts == []


def test_list_documents_reports_segmented_docs(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    listing = service.list_documents(workspace)

    assert [d.doc_id for d in listing.documents] == ["ddia"]
    doc = listing.documents[0]
    assert doc.section_count == 4
    assert doc.title == "ddia"  # no parsed document.json → falls back to doc_id


def test_list_documents_is_empty_without_segmented_docs(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)

    assert service.list_documents(workspace).documents == []


def test_create_plan_writes_a_readable_plan(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    created = service.create_plan(workspace, "ddia", daily_sections=2)

    assert created.plan_id == "ddia"
    assert created.doc_id == "ddia"
    assert created.daily_sections == 2
    assert created.section_count == 4
    # round-trips through list_plans and the next-section tool
    plans = service.list_plans(workspace)
    assert [(p.plan_id, p.completed, p.total, p.done) for p in plans.plans] == [
        ("ddia", 0, 4, False)
    ]
    nxt = service.get_next_section(workspace, "ddia")
    assert [s.id for s in nxt.sections] == ["ddia.part-1", "ddia.ch-1"]


def test_create_plan_custom_plan_id(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    created = service.create_plan(workspace, "ddia", plan_id="my-plan")

    assert created.plan_id == "my-plan"
    assert (workspace.reading_plans_root / "my-plan.json").is_file()


def test_create_plan_rejects_an_unsegmented_doc(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)

    with pytest.raises(SectionsNotFoundError):
        service.create_plan(workspace, "missing")


def test_create_plan_rejects_an_invalid_doc_id(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(InvalidIdError):
        service.create_plan(workspace, "../escape")


def test_create_plan_refuses_to_overwrite_an_in_progress_plan(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    service.create_plan(workspace, "ddia", daily_sections=2)
    service.mark_read(workspace, "ddia")  # make progress worth protecting

    with pytest.raises(ReadingServiceError, match="already exists"):
        service.create_plan(workspace, "ddia")

    # the existing plan (and its progress) is untouched by the refused re-create
    plans = service.list_plans(workspace)
    assert [(p.plan_id, p.completed, p.total) for p in plans.plans] == [("ddia", 1, 4)]


def test_create_plan_overwrite_replaces_the_existing_plan(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    service.create_plan(workspace, "ddia", daily_sections=2)
    service.mark_read(workspace, "ddia")

    recreated = service.create_plan(workspace, "ddia", daily_sections=1, overwrite=True)

    assert recreated.daily_sections == 1
    plans = service.list_plans(workspace)
    assert [(p.plan_id, p.completed, p.total) for p in plans.plans] == [("ddia", 0, 4)]


def test_list_plans_is_empty_without_any_plans(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    assert service.list_plans(workspace).plans == []


def test_annotate_section_accepts_a_dotted_section_id_and_round_trips(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    result = service.annotate_section(
        workspace,
        "ddia",
        "ddia.ch-1",
        [service.ConceptInput(title="Log Structured Merge", gloss="core")],
        summary="what this section says",
    )

    assert result.doc_id == "ddia"
    assert result.section_id == "ddia.ch-1"
    assert result.concept_count == 1
    path = workspace.annotations_root / "ddia" / "ddia.ch-1.json"
    assert Path(result.path) == path
    assert path.is_file()
    reloaded = read_annotation(path)
    assert reloaded.summary == "what this section says"
    assert reloaded.concepts[0].slug == "log-structured-merge"


def test_annotate_section_rejects_an_unknown_section_by_membership(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(SectionNotFoundError):
        service.annotate_section(workspace, "ddia", "ddia.ghost", [])
    # nothing written for the rejected id
    assert not (workspace.annotations_root / "ddia" / "ddia.ghost.json").exists()


def test_annotate_section_rejects_a_traversal_section_id(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    # A traversal value matches no section, so membership validation rejects it before
    # it is ever used as a path component.
    with pytest.raises(SectionNotFoundError):
        service.annotate_section(workspace, "ddia", "../escape", [])


def test_annotate_section_rejects_a_traversal_doc_id(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(InvalidIdError):
        service.annotate_section(workspace, "../escape", "ddia.ch-1", [])


def test_annotate_section_rejects_an_untitled_concept(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    with pytest.raises(ReadingServiceError):
        service.annotate_section(
            workspace, "ddia", "ddia.ch-1", [service.ConceptInput(title="!!!")]
        )


def test_get_context_shows_summary_immediately_before_any_rebuild(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, build_index=True)

    service.annotate_section(workspace, "ddia", "ddia.ch-1", [], summary="immediate summary")

    # No rebuild yet: summary is read straight from the annotation file.
    context = service.get_context(workspace, "ddia", "ddia.ch-1")
    assert context.summary == "immediate summary"


def test_get_context_reflects_gloss_and_source_after_rebuild(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, build_index=True)

    service.annotate_section(
        workspace,
        "ddia",
        "ddia.ch-1",
        [service.ConceptInput(title="Log Structured Merge", gloss="core idea")],
    )
    # Before rebuild the concepts are still the auto set (source auto).
    before = service.get_context(workspace, "ddia", "ddia.ch-1")
    assert all(c.source == "auto" for c in before.concepts)

    SqliteIndexBackend().build_document(workspace, "ddia", "DDIA", _sections())

    after = service.get_context(workspace, "ddia", "ddia.ch-1")
    assert [(c.slug, c.source, c.gloss) for c in after.concepts] == [
        ("log-structured-merge", "agent", "core idea")
    ]


def test_empty_annotation_removes_concepts_after_rebuild(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, build_index=True)
    # ddia.ch-1 has body "storage" → an auto concept before annotation.
    assert service.get_context(workspace, "ddia", "ddia.ch-1").concepts

    service.annotate_section(workspace, "ddia", "ddia.ch-1", [])
    SqliteIndexBackend().build_document(workspace, "ddia", "DDIA", _sections())

    assert service.get_context(workspace, "ddia", "ddia.ch-1").concepts == []
