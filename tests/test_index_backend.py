from __future__ import annotations

import sqlite3
from pathlib import Path

from bookgraph.annotations import annotation_path, build_annotation, write_annotation
from bookgraph.index import default_index_backend, tokenize
from bookgraph.index.sqlite import SqliteIndexBackend, db_path
from bookgraph.models import AnnotatedConcept, Section
from bookgraph.workspace import WorkspacePaths


def _annotate(
    workspace: WorkspacePaths,
    doc_id: str,
    section_id: str,
    concepts: list[AnnotatedConcept],
    *,
    summary: str = "",
) -> None:
    annotation = build_annotation(doc_id, section_id, concepts, summary=summary)
    write_annotation(
        annotation, annotation_path(workspace.annotations_root, doc_id, section_id)
    )


def _section(
    section_id: str,
    title: str,
    text: str,
    *,
    doc_id: str = "deep-work",
    level: int = 1,
    prev_id: str | None = None,
    next_id: str | None = None,
) -> Section:
    return Section(
        id=section_id,
        doc_id=doc_id,
        title=title,
        level=level,
        heading_path=[title],
        text=text,
        prev_id=prev_id,
        next_id=next_id,
    )


def test_tokenize_lowercases_and_splits_on_non_alphanumeric() -> None:
    assert tokenize("Storage-Engines, and B-Trees!") == [
        "storage",
        "engines",
        "and",
        "b",
        "trees",
    ]


def test_tokenize_folds_diacritics_to_match_fts_storage() -> None:
    # Query tokens must line up with FTS5 'unicode61 remove_diacritics 2' storage.
    assert tokenize("Kiến trúc") == ["kien", "truc"]
    assert tokenize("café") == ["cafe"]


def test_default_index_backend_is_sqlite() -> None:
    assert default_index_backend().name == "sqlite"


def test_build_writes_the_database_and_catalogs_the_document(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()

    count = backend.build_document(
        workspace,
        "deep-work",
        "Deep Work",
        [_section("deep-work.a", "Storage", "storage text")],
    )

    assert count == 1
    assert db_path(workspace).is_file()
    assert backend.indexed_doc_ids(workspace) == {"deep-work"}


def test_search_ranks_by_full_text_relevance(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace,
        "deep-work",
        "Deep Work",
        [
            _section("deep-work.a", "Storage engines", "storage storage storage index"),
            _section("deep-work.b", "Replication", "leaders and followers"),
            _section("deep-work.c", "Indexes", "an index on storage"),
        ],
    )

    hits = backend.search(workspace, ["storage"], ["deep-work"], 10)

    assert [hit.section_id for hit in hits] == ["deep-work.a", "deep-work.c"]
    assert hits[0].score > hits[1].score  # higher is a better match


def test_search_across_all_indexed_documents_when_scope_is_none(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace, "deep-work", "Deep Work", [_section("deep-work.a", "S", "storage")]
    )
    backend.build_document(
        workspace, "ddia", "DDIA", [_section("ddia.x", "S", "storage", doc_id="ddia")]
    )

    hits = backend.search(workspace, ["storage"], None, 10)

    assert sorted(hit.doc_id for hit in hits) == ["ddia", "deep-work"]


def test_search_matches_accented_terms(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace,
        "vi",
        "VI",
        [_section("vi.a", "Kiến trúc", "Kiến trúc hệ thống lưu trữ", doc_id="vi")],
    )

    # An accented query and its diacritic-folded form both match the stored text.
    assert [hit.section_id for hit in backend.search(workspace, tokenize("Kiến"), ["vi"], 10)] == [
        "vi.a"
    ]
    assert [hit.section_id for hit in backend.search(workspace, ["kien"], ["vi"], 10)] == ["vi.a"]


def test_reads_work_when_workspace_path_has_uri_special_chars(tmp_path: Path) -> None:
    # A workspace dir containing '#'/'?' must not break read-only URI opens.
    workspace = WorkspacePaths(tmp_path / "work #1?draft")
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace, "deep-work", "Deep Work", [_section("deep-work.a", "Storage", "storage text")]
    )

    assert backend.indexed_doc_ids(workspace) == {"deep-work"}
    hits = backend.search(workspace, ["storage"], None, 10)
    assert [hit.section_id for hit in hits] == ["deep-work.a"]
    assert backend.load_graph(workspace, "deep-work") is not None


def test_rebuild_is_idempotent_and_isolated_per_document(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace, "ddia", "DDIA", [_section("ddia.x", "X", "old text", doc_id="ddia")]
    )
    backend.build_document(
        workspace, "other", "Other", [_section("other.y", "Y", "kept", doc_id="other")]
    )

    # Re-segmented: same doc rebuilt with different content, twice.
    for _ in range(2):
        backend.build_document(
            workspace, "ddia", "DDIA", [_section("ddia.x", "X", "new text", doc_id="ddia")]
        )

    # No duplicate rows for ddia, and the untouched doc survives.
    assert backend.indexed_doc_ids(workspace) == {"ddia", "other"}
    fresh = backend.search(workspace, ["new"], ["ddia"], 10)
    assert [hit.section_id for hit in fresh] == ["ddia.x"]
    assert backend.search(workspace, ["old"], ["ddia"], 10) == []
    kept = backend.search(workspace, ["kept"], None, 10)
    assert [hit.section_id for hit in kept] == ["other.y"]


def test_load_graph_reconstructs_hierarchy_and_sequence(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace,
        "ddia",
        "DDIA",
        [
            _section("ddia.part-1", "Part I", "", doc_id="ddia", level=1, next_id="ddia.ch-1"),
            _section(
                "ddia.ch-1", "Chapter 1", "", doc_id="ddia", level=2, prev_id="ddia.part-1"
            ),
        ],
    )

    graph = backend.load_graph(workspace, "ddia")

    assert graph is not None
    by_id = {node.id: node for node in graph.nodes}
    assert by_id["ddia.part-1"].child_ids == ["ddia.ch-1"]
    assert by_id["ddia.ch-1"].parent_id == "ddia.part-1"
    assert by_id["ddia.ch-1"].prev_id == "ddia.part-1"


def test_query_methods_degrade_when_no_index_exists(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()

    # Nothing built yet: no database on disk.
    assert not db_path(workspace).exists()
    assert backend.indexed_doc_ids(workspace) == set()
    assert backend.search(workspace, ["storage"], None, 10) == []
    assert backend.load_graph(workspace, "ddia") is None


def test_load_graph_returns_none_for_a_document_not_in_the_catalog(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace, "deep-work", "Deep Work", [_section("deep-work.a", "A", "text")]
    )

    assert backend.load_graph(workspace, "ddia") is None


def test_concepts_are_extracted_and_aggregated_across_books(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace, "ddia", "DDIA", [_section("ddia.a", "Schema Evolution", "x", doc_id="ddia")]
    )
    backend.build_document(
        workspace,
        "deep-work",
        "Deep Work",
        [_section("deep-work.a", "Schema Evolution", "x")],
    )

    nodes = {node.slug: node for node in backend.concept_nodes(workspace)}
    assert "schema-evolution" in nodes
    assert nodes["schema-evolution"].doc_count == 2
    assert nodes["schema-evolution"].mention_count == 2

    concept = backend.get_concept(workspace, "schema-evolution")
    assert concept is not None
    assert concept.node.title == "Schema Evolution"
    assert sorted(m.doc_id for m in concept.mentions) == ["ddia", "deep-work"]
    # The section title travels with each backlink.
    assert all(m.title == "Schema Evolution" for m in concept.mentions)


def test_concepts_are_rebuilt_per_document_and_isolated(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace, "ddia", "DDIA", [_section("ddia.a", "Replication", "x", doc_id="ddia")]
    )
    backend.build_document(
        workspace, "other", "Other", [_section("other.a", "Sharding", "x", doc_id="other")]
    )
    # Re-segment ddia with a different concept.
    backend.build_document(
        workspace, "ddia", "DDIA", [_section("ddia.a", "Consensus", "x", doc_id="ddia")]
    )

    slugs = {node.slug for node in backend.concept_nodes(workspace)}
    assert "consensus" in slugs  # new concept present
    assert "replication" not in slugs  # old concept dropped on rebuild
    assert "sharding" in slugs  # untouched document survives
    assert backend.get_concept(workspace, "replication") is None


def test_concepts_bulk_matches_per_node_get_concept(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace, "ddia", "DDIA", [_section("ddia.a", "Schema Evolution", "x", doc_id="ddia")]
    )
    backend.build_document(
        workspace, "deep-work", "Deep Work", [_section("deep-work.a", "Schema Evolution", "x")]
    )

    # The single-pass concepts() must equal resolving each node individually.
    bulk = backend.concepts(workspace)
    per_node = [
        backend.get_concept(workspace, node.slug) for node in backend.concept_nodes(workspace)
    ]
    assert [c.model_dump() for c in bulk] == [c.model_dump() for c in per_node if c is not None]


def test_concepts_is_empty_without_an_index(tmp_path: Path) -> None:
    assert SqliteIndexBackend().concepts(WorkspacePaths(tmp_path)) == []


def test_get_concept_returns_none_for_unknown_slug(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace, "ddia", "DDIA", [_section("ddia.a", "Replication", "x", doc_id="ddia")]
    )

    assert backend.get_concept(workspace, "not-a-concept") is None


def test_concept_queries_degrade_when_no_index_exists(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()

    assert backend.concept_nodes(workspace) == []
    assert backend.get_concept(workspace, "anything") is None


def test_concept_queries_degrade_on_a_pre_concept_schema_db(tmp_path: Path) -> None:
    # A database built before the concept tables existed (core tables only) must
    # still serve; concept queries just return empty rather than raising.
    workspace = WorkspacePaths(tmp_path)
    _partial_db(
        workspace,
        [
            "CREATE TABLE doc_catalog "
            "(doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, section_count INTEGER NOT NULL)",
            "CREATE VIRTUAL TABLE sections_fts USING fts5("
            "doc_id UNINDEXED, section_id UNINDEXED, title, text)",
            "CREATE TABLE section_graph (doc_id TEXT NOT NULL, section_id TEXT NOT NULL, "
            "ord INTEGER NOT NULL, level INTEGER NOT NULL, title TEXT NOT NULL, "
            "heading_path TEXT NOT NULL, parent_id TEXT, prev_id TEXT, next_id TEXT, "
            "PRIMARY KEY (doc_id, section_id))",
        ],
    )
    backend = SqliteIndexBackend()

    assert backend.concept_nodes(workspace) == []
    assert backend.get_concept(workspace, "anything") is None


def _partial_db(workspace: WorkspacePaths, statements: list[str]) -> None:
    """Create a bookgraph.db with only the given schema statements (no full build)."""

    path = db_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def test_partial_db_with_only_doc_catalog_degrades_to_not_indexed(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    _partial_db(
        workspace,
        [
            "CREATE TABLE doc_catalog "
            "(doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, section_count INTEGER NOT NULL)",
            "INSERT INTO doc_catalog VALUES ('deep-work', 'Deep Work', 1)",
        ],
    )
    backend = SqliteIndexBackend()

    # A half-built db (missing sections_fts / section_graph) must read as "not
    # indexed" so the service falls back to a live scan, not raise OperationalError.
    assert backend.indexed_doc_ids(workspace) == set()
    assert backend.search(workspace, ["storage"], None, 10) == []
    assert backend.load_graph(workspace, "deep-work") is None


def test_annotated_section_overrides_auto_and_carries_gloss_and_source(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    sections = [_section("ddia.a", "Schema Evolution", "Schema Evolution matters", doc_id="ddia")]
    _annotate(
        workspace,
        "ddia",
        "ddia.a",
        [AnnotatedConcept(slug="", title="Log Structured Merge", gloss="core idea")],
    )

    backend.build_document(workspace, "ddia", "DDIA", sections)

    # The auto concepts (schema-evolution, etc.) are gone; only the agent concept remains.
    concepts = backend.section_concepts(workspace, "ddia", "ddia.a")
    assert [(c.slug, c.source, c.gloss) for c in concepts] == [
        ("log-structured-merge", "agent", "core idea")
    ]
    # ...and it round-trips through the cross-book concept lookup with source/gloss.
    concept = backend.get_concept(workspace, "log-structured-merge")
    assert concept is not None
    assert [(m.source, m.gloss) for m in concept.mentions] == [("agent", "core idea")]
    assert backend.get_concept(workspace, "schema-evolution") is None


def test_empty_annotation_prunes_the_sections_concepts(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    sections = [_section("ddia.a", "Schema Evolution", "Schema Evolution matters", doc_id="ddia")]

    # Baseline: auto concepts exist.
    backend.build_document(workspace, "ddia", "DDIA", sections)
    assert backend.section_concepts(workspace, "ddia", "ddia.a")

    # An empty annotation zeroes them out on the next rebuild.
    _annotate(workspace, "ddia", "ddia.a", [])
    backend.build_document(workspace, "ddia", "DDIA", sections)

    assert backend.section_concepts(workspace, "ddia", "ddia.a") == []
    assert backend.concept_nodes(workspace) == []


def test_auto_mentions_carry_default_source_and_empty_gloss(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    backend.build_document(
        workspace, "ddia", "DDIA", [_section("ddia.a", "Schema Evolution", "x", doc_id="ddia")]
    )

    concept = backend.get_concept(workspace, "schema-evolution")
    assert concept is not None
    assert all(m.source == "auto" and m.gloss == "" for m in concept.mentions)


def test_section_annotation_round_trips_through_the_index(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    backend = SqliteIndexBackend()
    sections = [_section("ddia.a", "Schema Evolution", "x", doc_id="ddia")]
    _annotate(workspace, "ddia", "ddia.a", [], summary="the agent's explanation")

    backend.build_document(workspace, "ddia", "DDIA", sections)

    assert backend.section_annotation(workspace, "ddia", "ddia.a") == "the agent's explanation"
    # An unannotated section has no stored summary.
    assert backend.section_annotation(workspace, "ddia", "ddia.ghost") is None


def test_old_shape_concept_mentions_reads_empty_not_crash(tmp_path: Path) -> None:
    # A concept_mentions table without the gloss/source columns must not crash concept
    # reads: the gloss/source SELECTs hit "no such column" and degrade to empty.
    workspace = WorkspacePaths(tmp_path)
    _partial_db(
        workspace,
        [
            "CREATE TABLE doc_catalog "
            "(doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, section_count INTEGER NOT NULL)",
            "CREATE VIRTUAL TABLE sections_fts USING fts5("
            "doc_id UNINDEXED, section_id UNINDEXED, title, text)",
            "CREATE TABLE section_graph (doc_id TEXT NOT NULL, section_id TEXT NOT NULL, "
            "ord INTEGER NOT NULL, level INTEGER NOT NULL, title TEXT NOT NULL, "
            "heading_path TEXT NOT NULL, parent_id TEXT, prev_id TEXT, next_id TEXT, "
            "PRIMARY KEY (doc_id, section_id))",
            "CREATE TABLE concept_mentions (concept_slug TEXT NOT NULL, "
            "concept_title TEXT NOT NULL, doc_id TEXT NOT NULL, section_id TEXT NOT NULL, "
            "PRIMARY KEY (doc_id, section_id, concept_slug))",
            "INSERT INTO concept_mentions VALUES ('schema-evolution', 'Schema Evolution', "
            "'ddia', 'ddia.a')",
        ],
    )
    backend = SqliteIndexBackend()

    # concept_nodes (the view, no gloss/source) still aggregates, but the mention reads
    # that name gloss/source degrade to empty rather than raising.
    assert backend.get_concept(workspace, "schema-evolution") is None
    assert backend.section_concepts(workspace, "ddia", "ddia.a") == []
    assert backend.concepts(workspace) == []
    assert backend.section_annotation(workspace, "ddia", "ddia.a") is None


def test_build_migrates_an_old_schema_db_then_queries(tmp_path: Path) -> None:
    # A pre-annotation database (concept_mentions without gloss/source, no
    # section_annotations table) must be migrated in place by build_document, after
    # which the new columns/table are populated and queryable.
    workspace = WorkspacePaths(tmp_path)
    _partial_db(
        workspace,
        [
            "CREATE TABLE doc_catalog "
            "(doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, section_count INTEGER NOT NULL)",
            "CREATE VIRTUAL TABLE sections_fts USING fts5("
            "doc_id UNINDEXED, section_id UNINDEXED, title, text)",
            "CREATE TABLE section_graph (doc_id TEXT NOT NULL, section_id TEXT NOT NULL, "
            "ord INTEGER NOT NULL, level INTEGER NOT NULL, title TEXT NOT NULL, "
            "heading_path TEXT NOT NULL, parent_id TEXT, prev_id TEXT, next_id TEXT, "
            "PRIMARY KEY (doc_id, section_id))",
            "CREATE TABLE concept_mentions (concept_slug TEXT NOT NULL, "
            "concept_title TEXT NOT NULL, doc_id TEXT NOT NULL, section_id TEXT NOT NULL, "
            "PRIMARY KEY (doc_id, section_id, concept_slug))",
        ],
    )
    backend = SqliteIndexBackend()
    sections = [_section("ddia.a", "Schema Evolution", "x", doc_id="ddia")]
    _annotate(
        workspace,
        "ddia",
        "ddia.a",
        [AnnotatedConcept(slug="", title="Curated", gloss="g")],
        summary="s",
    )

    backend.build_document(workspace, "ddia", "DDIA", sections)

    concept = backend.get_concept(workspace, "curated")
    assert concept is not None
    assert [(m.source, m.gloss) for m in concept.mentions] == [("agent", "g")]
    assert backend.section_annotation(workspace, "ddia", "ddia.a") == "s"


def test_partial_db_missing_section_graph_degrades_to_not_indexed(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    _partial_db(
        workspace,
        [
            "CREATE TABLE doc_catalog "
            "(doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, section_count INTEGER NOT NULL)",
            "INSERT INTO doc_catalog VALUES ('deep-work', 'Deep Work', 1)",
            "CREATE VIRTUAL TABLE sections_fts USING fts5("
            "doc_id UNINDEXED, section_id UNINDEXED, title, text)",
        ],
    )
    backend = SqliteIndexBackend()

    # sections_fts present but section_graph missing: graph tools must fall back too.
    assert backend.indexed_doc_ids(workspace) == set()
    assert backend.load_graph(workspace, "deep-work") is None
