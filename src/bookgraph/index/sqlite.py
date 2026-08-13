"""SQLite/FTS5 implementation of :class:`bookgraph.index.base.IndexBackend`.

One workspace-wide database at ``indexes/bookgraph.db`` holds:

- ``doc_catalog`` — one row per indexed document (presence = "indexed").
- ``sections_fts`` — an FTS5 full-text table backing ``search`` (ranked ``bm25``).
- ``section_graph`` — hierarchy + sequence edges backing the graph/context tools.
- ``concept_mentions`` — per-section concept backlinks (+ the ``concept_nodes``
  view aggregating them across books) backing ``get_concept``.

See ``.docs/cli/index.md`` for the schema and build/query contract.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote

from bookgraph.concepts import extract_concepts
from bookgraph.graph import SectionGraph, SectionNode, build_section_graph
from bookgraph.index.base import (
    Concept,
    ConceptMention,
    ConceptNode,
    IndexBackend,
    IndexSearchHit,
    IndexUnavailableError,
)
from bookgraph.models import Section
from bookgraph.workspace import WorkspacePaths

DB_FILENAME = "bookgraph.db"

# Every table this backend owns. A database missing any of them is treated as
# "not indexed" (see ``_open_read_only``) so the service degrades to a live scan
# rather than crashing on a half-built or schema-mismatched file.
_REQUIRED_TABLES = ("doc_catalog", "sections_fts", "section_graph")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_catalog (
    doc_id        TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    section_count INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS sections_fts USING fts5(
    doc_id UNINDEXED,
    section_id UNINDEXED,
    title,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS section_graph (
    doc_id       TEXT NOT NULL,
    section_id   TEXT NOT NULL,
    ord          INTEGER NOT NULL,
    level        INTEGER NOT NULL,
    title        TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    parent_id    TEXT,
    prev_id      TEXT,
    next_id      TEXT,
    PRIMARY KEY (doc_id, section_id)
);
CREATE INDEX IF NOT EXISTS section_graph_parent ON section_graph (doc_id, parent_id);
CREATE INDEX IF NOT EXISTS section_graph_ord ON section_graph (doc_id, ord);
CREATE TABLE IF NOT EXISTS concept_mentions (
    concept_slug  TEXT NOT NULL,
    concept_title TEXT NOT NULL,
    doc_id        TEXT NOT NULL,
    section_id    TEXT NOT NULL,
    PRIMARY KEY (doc_id, section_id, concept_slug)
);
CREATE INDEX IF NOT EXISTS concept_mentions_slug ON concept_mentions (concept_slug);
CREATE VIEW IF NOT EXISTS concept_nodes AS
SELECT concept_slug           AS slug,
       MIN(concept_title)      AS title,
       COUNT(DISTINCT doc_id)  AS doc_count,
       COUNT(*)                AS mention_count
FROM concept_mentions
GROUP BY concept_slug;
"""


def db_path(workspace: WorkspacePaths) -> Path:
    """Canonical location of the workspace-wide index database."""

    return workspace.indexes_root / DB_FILENAME


class SqliteIndexBackend(IndexBackend):
    """Default index backend: raw ``sqlite3`` with an FTS5 search table."""

    name = "sqlite"

    def build_document(
        self, workspace: WorkspacePaths, doc_id: str, title: str, sections: list[Section]
    ) -> int:
        path = db_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        # A 30s busy timeout lets a concurrent build's write transaction finish
        # rather than immediately raising "database is locked" (the workspace-wide
        # db is a single file, unlike the old per-document JSON indexes).
        conn = sqlite3.connect(path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            _ensure_schema(conn)
            return _build_document(conn, doc_id, title, sections)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                raise IndexUnavailableError(
                    "The index database is locked by a concurrent build; retry once "
                    "it finishes. Index builds must not run in parallel on one "
                    "workspace."
                ) from exc
            raise
        finally:
            conn.close()

    def indexed_doc_ids(self, workspace: WorkspacePaths) -> set[str]:
        empty: set[str] = set()
        return _read(
            workspace,
            empty,
            lambda conn: {row["doc_id"] for row in conn.execute("SELECT doc_id FROM doc_catalog")},
        )

    def search(
        self,
        workspace: WorkspacePaths,
        terms: list[str],
        doc_ids: list[str] | None,
        limit: int,
    ) -> list[IndexSearchHit]:
        empty: list[IndexSearchHit] = []
        return _read(
            workspace,
            empty,
            lambda conn: [
                IndexSearchHit(
                    doc_id=row["doc_id"],
                    section_id=row["section_id"],
                    title=row["title"],
                    text=row["text"],
                    # bm25 is lower-is-better; negate so higher is a better match.
                    score=-row["rank"],
                )
                for row in _search(conn, terms, doc_ids, limit)
            ],
        )

    def load_graph(self, workspace: WorkspacePaths, doc_id: str) -> SectionGraph | None:
        def query(conn: sqlite3.Connection) -> SectionGraph | None:
            if not _is_indexed(conn, doc_id):
                return None
            return _load_graph(conn, doc_id)

        return _read(workspace, None, query)

    def concept_nodes(self, workspace: WorkspacePaths) -> list[ConceptNode]:
        empty: list[ConceptNode] = []
        return _read(workspace, empty, lambda conn: [_to_node(row) for row in _concept_nodes(conn)])

    def get_concept(self, workspace: WorkspacePaths, slug: str) -> Concept | None:
        def query(conn: sqlite3.Connection) -> Concept | None:
            node = _concept_node(conn, slug)
            if node is None:
                return None
            mentions = [
                ConceptMention(
                    doc_id=row["doc_id"],
                    section_id=row["section_id"],
                    title=row["title"],
                )
                for row in _concept_mentions(conn, slug)
            ]
            return Concept(node=node, mentions=mentions)

        return _read(workspace, None, query)

    def concepts(self, workspace: WorkspacePaths) -> list[Concept]:
        def query(conn: sqlite3.Connection) -> list[Concept]:
            mentions_by_slug: dict[str, list[ConceptMention]] = {}
            for row in _all_concept_mentions(conn):
                mentions_by_slug.setdefault(row["slug"], []).append(
                    ConceptMention(
                        doc_id=row["doc_id"],
                        section_id=row["section_id"],
                        title=row["title"],
                    )
                )
            return [
                Concept(node=node, mentions=mentions_by_slug.get(node.slug, []))
                for node in (_to_node(row) for row in _concept_nodes(conn))
            ]

        empty: list[Concept] = []
        return _read(workspace, empty, query)

    def section_concepts(
        self, workspace: WorkspacePaths, doc_id: str, section_id: str
    ) -> list[ConceptNode]:
        empty: list[ConceptNode] = []
        return _read(
            workspace,
            empty,
            lambda conn: [_to_node(row) for row in _section_concepts(conn, doc_id, section_id)],
        )

    def location(self, workspace: WorkspacePaths) -> str:
        return str(db_path(workspace))


_T = TypeVar("_T")


def _read(
    workspace: WorkspacePaths, empty: _T, query: Callable[[sqlite3.Connection], _T]
) -> _T:
    """Run ``query`` on a read-only connection, degrading to ``empty``.

    Centralizes the open / guard / close contract shared by every read method: an
    absent, corrupt, or schema-incomplete database — or one that turns unusable
    mid-read — yields ``empty`` rather than raising.
    """

    conn = _open_read_only(workspace)
    if conn is None:
        return empty
    try:
        return query(conn)
    except sqlite3.Error:
        return empty
    finally:
        conn.close()


def _to_node(row: sqlite3.Row) -> ConceptNode:
    return ConceptNode(
        slug=row["slug"],
        title=row["title"],
        doc_count=row["doc_count"],
        mention_count=row["mention_count"],
    )


def _open_read_only(workspace: WorkspacePaths) -> sqlite3.Connection | None:
    """Open the database read-only, or ``None`` if absent/corrupt/schema-less.

    A missing or unusable database is not an error — it just means "not indexed",
    and the service layer degrades to a live scan of the sections manifest.
    """

    path = db_path(workspace)
    if not path.is_file():
        return None
    try:
        # Percent-encode the path so a workspace directory containing URI-special
        # characters (# fragment, ? query) still opens the intended file read-only.
        uri = f"file:{quote(str(path))}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        # Every backend-owned table must be present and queryable. A file with a
        # partial schema (e.g. only ``doc_catalog``) is not usable: the search and
        # graph reads would raise "no such table" instead of degrading cleanly.
        for table in _REQUIRED_TABLES:
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1")  # smoke-test the schema
        return conn
    except sqlite3.Error:
        return None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(_SCHEMA)
    except sqlite3.OperationalError as exc:  # the FTS5 virtual table is what fails
        if "locked" in str(exc).lower():
            raise  # a busy/locked db is not an FTS5 problem — let the caller handle it
        raise IndexUnavailableError(
            "SQLite was built without the FTS5 extension required for the search "
            f"index ({exc}). Rebuild Python against a SQLite with FTS5."
        ) from exc


def _build_document(
    conn: sqlite3.Connection, doc_id: str, title: str, sections: list[Section]
) -> int:
    graph = build_section_graph(doc_id, sections)
    graph_rows = [
        (
            doc_id,
            node.id,
            ordinal,
            node.level,
            node.title,
            json.dumps(node.heading_path),
            node.parent_id,
            node.prev_id,
            node.next_id,
        )
        for ordinal, node in enumerate(graph.nodes)
    ]
    concept_rows = [
        (concept.slug, concept.title, doc_id, section_id)
        for concept in extract_concepts(sections)
        for section_id in concept.section_ids
    ]
    with conn:  # one transaction: BEGIN/COMMIT, or ROLLBACK on error
        conn.execute("DELETE FROM doc_catalog WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM sections_fts WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM section_graph WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM concept_mentions WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            "INSERT INTO sections_fts (doc_id, section_id, title, text) VALUES (?, ?, ?, ?)",
            [(doc_id, section.id, section.title, section.text) for section in sections],
        )
        conn.executemany(
            "INSERT INTO section_graph "
            "(doc_id, section_id, ord, level, title, heading_path, parent_id, prev_id, next_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            graph_rows,
        )
        conn.executemany(
            "INSERT INTO concept_mentions "
            "(concept_slug, concept_title, doc_id, section_id) VALUES (?, ?, ?, ?)",
            concept_rows,
        )
        conn.execute(
            "INSERT INTO doc_catalog (doc_id, title, section_count) VALUES (?, ?, ?)",
            (doc_id, title, len(sections)),
        )
    return len(sections)


def _is_indexed(conn: sqlite3.Connection, doc_id: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM doc_catalog WHERE doc_id = ? LIMIT 1", (doc_id,)).fetchone()
        is not None
    )


def _match_expr(terms: list[str]) -> str:
    """A safe FTS5 MATCH expression (OR over terms).

    Terms are already lowercased ``[a-z0-9]+`` runs, so quoting each as a string
    literal stops FTS5 from reading any of them as an operator.
    """

    return " OR ".join(f'"{term}"' for term in terms)


def _search(
    conn: sqlite3.Connection, terms: list[str], doc_ids: list[str] | None, limit: int
) -> list[sqlite3.Row]:
    sql = (
        "SELECT doc_id, section_id, title, text, bm25(sections_fts) AS rank "
        "FROM sections_fts WHERE sections_fts MATCH ?"
    )
    params: list[object] = [_match_expr(terms)]
    if doc_ids is not None:
        placeholders = ",".join("?" for _ in doc_ids)
        sql += f" AND doc_id IN ({placeholders})"
        params.extend(doc_ids)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def _load_graph(conn: sqlite3.Connection, doc_id: str) -> SectionGraph:
    rows = conn.execute(
        "SELECT section_id, level, title, heading_path, parent_id, prev_id, next_id "
        "FROM section_graph WHERE doc_id = ? ORDER BY ord",
        (doc_id,),
    ).fetchall()

    children: dict[str, list[str]] = {}
    for row in rows:
        if row["parent_id"] is not None:
            children.setdefault(row["parent_id"], []).append(row["section_id"])

    nodes = [
        SectionNode(
            id=row["section_id"],
            title=row["title"],
            level=row["level"],
            heading_path=json.loads(row["heading_path"]),
            parent_id=row["parent_id"],
            prev_id=row["prev_id"],
            next_id=row["next_id"],
            child_ids=children.get(row["section_id"], []),
        )
        for row in rows
    ]
    return SectionGraph(doc_id=doc_id, nodes=nodes)


def _concept_nodes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT slug, title, doc_count, mention_count FROM concept_nodes "
        "ORDER BY doc_count DESC, mention_count DESC, slug"
    ).fetchall()


def _concept_node(conn: sqlite3.Connection, slug: str) -> ConceptNode | None:
    row = conn.execute(
        "SELECT slug, title, doc_count, mention_count FROM concept_nodes WHERE slug = ?",
        (slug,),
    ).fetchone()
    if row is None:
        return None
    return ConceptNode(
        slug=row["slug"],
        title=row["title"],
        doc_count=row["doc_count"],
        mention_count=row["mention_count"],
    )


def _section_concepts(
    conn: sqlite3.Connection, doc_id: str, section_id: str
) -> list[sqlite3.Row]:
    # The section's concepts, joined to the cross-book aggregate for their totals,
    # ordered most-connected first.
    return conn.execute(
        "SELECT cn.slug AS slug, cn.title AS title, "
        "       cn.doc_count AS doc_count, cn.mention_count AS mention_count "
        "FROM concept_mentions cm "
        "JOIN concept_nodes cn ON cn.slug = cm.concept_slug "
        "WHERE cm.doc_id = ? AND cm.section_id = ? "
        "ORDER BY cn.doc_count DESC, cn.mention_count DESC, cn.slug",
        (doc_id, section_id),
    ).fetchall()


def _all_concept_mentions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    # Every concept's mentions in one query, grouped/ordered so callers can bucket
    # them by slug — same per-concept order as ``_concept_mentions`` (by document,
    # then reading position). Backs the single-pass ``concepts()``.
    return conn.execute(
        "SELECT cm.concept_slug AS slug, cm.doc_id AS doc_id, cm.section_id AS section_id, "
        "       COALESCE(sg.title, cm.section_id) AS title "
        "FROM concept_mentions cm "
        "LEFT JOIN section_graph sg "
        "  ON sg.doc_id = cm.doc_id AND sg.section_id = cm.section_id "
        "ORDER BY cm.concept_slug, cm.doc_id, sg.ord, cm.section_id"
    ).fetchall()


def _concept_mentions(conn: sqlite3.Connection, slug: str) -> list[sqlite3.Row]:
    # Join to section_graph for the mentioning section's title and reading order;
    # group by document, ordered within a document by reading position.
    return conn.execute(
        "SELECT cm.doc_id AS doc_id, cm.section_id AS section_id, "
        "       COALESCE(sg.title, cm.section_id) AS title "
        "FROM concept_mentions cm "
        "LEFT JOIN section_graph sg "
        "  ON sg.doc_id = cm.doc_id AND sg.section_id = cm.section_id "
        "WHERE cm.concept_slug = ? "
        "ORDER BY cm.doc_id, sg.ord, cm.section_id",
        (slug,),
    ).fetchall()
