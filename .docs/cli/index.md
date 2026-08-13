# Index contract — `indexes/bookgraph.db`

Owner: the index stage (`bookgraph index build` command / `bookgraph.index`).

The index stage compiles the file artifacts under `sources/sections/<doc_id>/`
into **one workspace-wide SQLite database** at `indexes/bookgraph.db`. It backs
the MCP `search` tool and the graph/context tools (`get_outline`, `get_related`,
`get_context`).

The storage engine is pluggable behind an `IndexBackend` port (in
`bookgraph.index`); SQLite/FTS5 is the default backend, and the schema below is
its contract. A different engine is added by implementing the port and
registering it — the CLI and MCP service depend only on the interface.

The database is a **derived, fully rebuildable** artifact — never a source of
truth. The canonical artifacts remain the files `sources/parsed/<doc_id>/document.json`,
`sources/sections/<doc_id>/sections.jsonl` + `<section_id>.md`, and
`reading_plans/<plan_id>.json` (see `artifacts.md`). Deleting `bookgraph.db` and
re-running `index build` for every document reproduces it exactly.

This supersedes the earlier per-document `indexes/sections/<doc_id>.json`
(inverted index) and `indexes/graph/<doc_id>.json` (structural graph) files. Those
JSON artifacts are removed once a document is built into the database.

## Requirements

- SQLite built with the **FTS5** extension (the standard CPython `sqlite3` module
  on macOS/Linux ships with it). `index build` fails clean with an actionable
  message if FTS5 is unavailable.

## Schema

### `doc_catalog`

One row per indexed document.

```sql
CREATE TABLE doc_catalog (
    doc_id        TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    section_count INTEGER NOT NULL
);
```

- `doc_id`: matches the `sources/sections/<doc_id>/` folder.
- `title`: the document title (from `document.json`), carried for result labels.
- `section_count`: number of sections indexed for this document.
- Presence of a row is the **authoritative signal** that a document is indexed.
  A `doc_id` absent from `doc_catalog` is treated as unindexed, and query tools
  fall back to a live scan of its `sections.jsonl` (see *Fallback* below).

### `sections_fts`

FTS5 virtual table backing `search`. One row per section.

```sql
CREATE VIRTUAL TABLE sections_fts USING fts5(
    doc_id     UNINDEXED,
    section_id UNINDEXED,
    title,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);
```

- `doc_id` / `section_id`: not tokenized; used to filter (`WHERE doc_id = ?`) and
  to join back to `section_graph` / the section files. `section_id` has the
  `<doc_id>.<slug>` form.
- `title` / `text`: tokenized full-text columns — a denormalised copy of each
  section's title and body so `search` builds snippets without re-reading
  `sections.jsonl`.
- Ranking uses FTS5 `bm25(sections_fts)` (lower = better), returned ascending.

### `section_graph`

Structural graph backing `get_outline` / `get_related` / `get_context`. One row
per section.

```sql
CREATE TABLE section_graph (
    doc_id       TEXT NOT NULL,
    section_id   TEXT NOT NULL,
    ord          INTEGER NOT NULL,
    level        INTEGER NOT NULL,
    title        TEXT NOT NULL,
    heading_path TEXT NOT NULL,   -- JSON array of strings
    parent_id    TEXT,            -- NULL for a top-level section
    prev_id      TEXT,            -- NULL at the start of the document
    next_id      TEXT,            -- NULL at the end of the document
    PRIMARY KEY (doc_id, section_id)
);
CREATE INDEX section_graph_parent ON section_graph (doc_id, parent_id);
CREATE INDEX section_graph_ord    ON section_graph (doc_id, ord);
```

- `ord`: 0-based reading-order position; `ORDER BY ord` reconstructs the manifest
  order.
- `level` / `title` / `heading_path`: carried through from the section manifest;
  `heading_path` is a JSON array (e.g. `["Chapter 3. Storage", "SSTables"]`).
- `parent_id`: nearest preceding section with a smaller `level` (the containing
  chapter/part), or `NULL`. Derived via a reading-order stack, so a jump from
  level 1 to level 3 still nests under the nearest shallower section.
- `child_ids` are **not stored** — they are the inverse of `parent_id`, recovered
  with `SELECT section_id FROM section_graph WHERE doc_id = ? AND parent_id = ? ORDER BY ord`.
- `prev_id` / `next_id`: linear reading-order neighbours from the manifest; a
  neighbour id not present in this document is stored as `NULL`.

Two edge kinds are represented: **hierarchy** (`parent_id` → children) and
**sequence** (`prev_id` / `next_id`).

## Build semantics

`bookgraph index build <workspace> <doc_id>`:

1. Reads `sources/sections/<doc_id>/sections.jsonl`.
2. Opens (creating if absent) `indexes/bookgraph.db` and ensures the schema.
3. Rebuilds that document **idempotently and atomically** in one transaction:
   `DELETE FROM doc_catalog / sections_fts / section_graph WHERE doc_id = ?`,
   then re-inserts the fresh rows. Re-running build for the same document is a
   no-op net of content changes; other documents' rows are never touched.
4. Prints the database path and the section count written.

Rebuild a document after re-segmenting it so the denormalised `title` / `text`
and the graph edges stay in sync with `sections.jsonl`.

## Query semantics

- **`search(query, doc_id=None, limit=...)`**: an FTS5 `MATCH` over
  `sections_fts`, ranked by `bm25`. When `doc_id` is given, results are filtered
  to that document; when omitted, `search` ranks across **every** indexed document
  (cross-document search) and each hit carries its `doc_id`.
- **`get_outline` / `get_related` / `get_context`**: read `section_graph` for the
  requested `doc_id`, reconstructing children via the `parent_id` inverse.

## Fallback

Query tools preserve the existing **index-or-scan** behaviour: if
`indexes/bookgraph.db` is missing, or the requested `doc_id` has no row in
`doc_catalog`, the tool falls back to a live scan/build from that document's
`sections.jsonl`. Results stay correct; only ranking fidelity and latency differ
(the live-scan path keeps the legacy lowercased `[a-z0-9]+` term-frequency scorer,
which will not rank byte-identically to FTS5 `bm25`).

## Concurrency

- Readers open the database read-only (`mode=ro`) and never write.
- `index build` is the only writer; builds are per-document transactions, so
  building one document never corrupts another's rows.
