from __future__ import annotations

from pathlib import Path

from bookgraph.index import default_index_backend, tokenize
from bookgraph.index.sqlite import SqliteIndexBackend, db_path
from bookgraph.models import Section
from bookgraph.workspace import WorkspacePaths


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
