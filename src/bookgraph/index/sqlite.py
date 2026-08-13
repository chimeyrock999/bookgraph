"""SQLite/FTS5 implementation of :class:`bookgraph.index.base.IndexBackend`.

One workspace-wide database at ``indexes/bookgraph.db`` holds three tables:

- ``doc_catalog`` — one row per indexed document (presence = "indexed").
- ``sections_fts`` — an FTS5 full-text table backing ``search`` (ranked ``bm25``).
- ``section_graph`` — hierarchy + sequence edges backing the graph/context tools.

See ``.docs/cli/index.md`` for the schema and build/query contract.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from bookgraph.graph import SectionGraph, SectionNode, build_section_graph
from bookgraph.index.base import IndexBackend, IndexSearchHit, IndexUnavailableError
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
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            _ensure_schema(conn)
            return _build_document(conn, doc_id, title, sections)
        finally:
            conn.close()

    def indexed_doc_ids(self, workspace: WorkspacePaths) -> set[str]:
        conn = _open_read_only(workspace)
        if conn is None:
            return set()
        try:
            return {row["doc_id"] for row in conn.execute("SELECT doc_id FROM doc_catalog")}
        finally:
            conn.close()

    def search(
        self,
        workspace: WorkspacePaths,
        terms: list[str],
        doc_ids: list[str] | None,
        limit: int,
    ) -> list[IndexSearchHit]:
        conn = _open_read_only(workspace)
        if conn is None:
            return []
        try:
            return [
                IndexSearchHit(
                    doc_id=row["doc_id"],
                    section_id=row["section_id"],
                    title=row["title"],
                    text=row["text"],
                    # bm25 is lower-is-better; negate so higher is a better match.
                    score=-row["rank"],
                )
                for row in _search(conn, terms, doc_ids, limit)
            ]
        except sqlite3.Error:
            return []  # a db that turns unusable mid-read degrades to "not indexed"
        finally:
            conn.close()

    def load_graph(self, workspace: WorkspacePaths, doc_id: str) -> SectionGraph | None:
        conn = _open_read_only(workspace)
        if conn is None:
            return None
        try:
            if not _is_indexed(conn, doc_id):
                return None
            return _load_graph(conn, doc_id)
        except sqlite3.Error:
            return None  # a db that turns unusable mid-read degrades to "not indexed"
        finally:
            conn.close()

    def location(self, workspace: WorkspacePaths) -> str:
        return str(db_path(workspace))


def _open_read_only(workspace: WorkspacePaths) -> sqlite3.Connection | None:
    """Open the database read-only, or ``None`` if absent/corrupt/schema-less.

    A missing or unusable database is not an error — it just means "not indexed",
    and the service layer degrades to a live scan of the sections manifest.
    """

    path = db_path(workspace)
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
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
    with conn:  # one transaction: BEGIN/COMMIT, or ROLLBACK on error
        conn.execute("DELETE FROM doc_catalog WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM sections_fts WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM section_graph WHERE doc_id = ?", (doc_id,))
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
