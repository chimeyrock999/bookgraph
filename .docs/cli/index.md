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

### `concept_mentions`

The **cross-book concept graph**. One row per (section, concept) pairing — i.e.
each place a concept is mentioned. This is the stored source of truth for
concepts; it is deleted/re-inserted per document like the other tables, so a
per-document rebuild stays isolated and idempotent.

```sql
CREATE TABLE concept_mentions (
    concept_slug  TEXT NOT NULL,   -- deterministic slug, e.g. "schema-evolution"
    concept_title TEXT NOT NULL,   -- display title, e.g. "Schema Evolution"
    doc_id        TEXT NOT NULL,
    section_id    TEXT NOT NULL,
    PRIMARY KEY (doc_id, section_id, concept_slug)
);
CREATE INDEX concept_mentions_slug ON concept_mentions (concept_slug);
```

- Concepts are extracted from the document's own `sections.jsonl` by the shared
  deterministic extractor `bookgraph.concepts.extract_concepts` (the same one the
  `markdown-graph` wiki backend uses for in-page wikilinks), so a section's
  mentions here match the `[[<slug>|Title]]` links on its book page.
- `concept_slug`: `[a-z0-9]+(?:-[a-z0-9]+)*`; the join key across books.
- `concept_title`: display title carried for labels. The same slug may carry
  slightly different titles across documents; a single canonical title is chosen
  at query time (see `concept_nodes`).
- `section_id`: the mentioning section, joinable to `section_graph` and the
  section files. `(doc_id, section_id)` scope makes per-doc delete trivial.

### `concept_nodes`

A **view**, not a table: the cross-book aggregate of `concept_mentions`. Modelling
it as a view (rather than a materialised table) keeps per-document builds simple —
a per-doc rebuild never has to recompute a global table — while still exposing a
stable `concept_nodes` surface to callers.

```sql
CREATE VIEW concept_nodes AS
SELECT concept_slug              AS slug,
       MIN(concept_title)        AS title,          -- canonical display title
       COUNT(DISTINCT doc_id)    AS doc_count,      -- books mentioning it
       COUNT(*)                  AS mention_count   -- total mentioning sections
FROM concept_mentions
GROUP BY concept_slug;
```

- `title`: canonicalised deterministically (`MIN`) so a slug renders one label.
- `doc_count` / `mention_count`: drive ordering (most-connected concepts first)
  for `wiki/concepts` rendering and concept listings.

## Build semantics

`bookgraph index build <workspace> <doc_id>`:

1. Reads `sources/sections/<doc_id>/sections.jsonl`.
2. Opens (creating if absent) `indexes/bookgraph.db` and ensures the schema.
3. Rebuilds that document **idempotently and atomically** in one transaction:
   `DELETE FROM doc_catalog / sections_fts / section_graph / concept_mentions
   WHERE doc_id = ?`, then re-inserts the fresh rows (including the document's
   concept mentions, extracted from its sections via `bookgraph.concepts`).
   Re-running build for the same document is a no-op net of content changes; other
   documents' rows are never touched.
4. Prints the database path and the section count written.

Rebuild a document after re-segmenting it so the denormalised `title` / `text`,
the graph edges, and the concept mentions stay in sync with `sections.jsonl`.

`bookgraph index concepts <workspace>` — a **global** pass (not per-document):

1. Reads `concept_nodes` / `concept_mentions` across every indexed document.
2. Rewrites the whole `wiki/concepts/` directory, one `<concept_slug>.md` page per
   concept with cross-book backlinks (see `artifacts.md`).
3. Prints the number of concept pages written and the output directory.

Concept *data* is populated per-document by `index build`; concept *pages* are
rendered by this separate pass because they aggregate across all books. Run it
after the relevant documents have been built. This command owns only the
`wiki/concepts/` surface — it never touches `wiki/books/`, which is the wiki
stage's output.

## Query semantics

- **`search(query, doc_id=None, limit=...)`**: an FTS5 `MATCH` over
  `sections_fts`, ranked by `bm25`. When `doc_id` is given, results are filtered
  to that document; when omitted, `search` ranks across **every** indexed document
  (cross-document search) and each hit carries its `doc_id`.
- **`get_outline` / `get_related` / `get_context`**: read `section_graph` for the
  requested `doc_id`, reconstructing children via the `parent_id` inverse.
  `get_context` additionally returns the section's own concepts (from
  `concept_mentions`, each with its cross-book `doc_count` / `mention_count`) so a
  reader can pivot from the current section into `get_concept`; these are empty for
  an unindexed document (concepts have no live-scan fallback).
- **`get_concept(concept)`**: looks a concept up by slug in `concept_nodes`, then
  reads its `concept_mentions` across **every** indexed book. Returns the node
  (`slug`, `title`, `doc_count`, `mention_count`) plus its backlink mentions
  (`doc_id`, `section_id`, section `title`) grouped/ordered by document — the
  cross-book "what mentions this concept" query. Returns nothing when the slug is
  absent. Concepts have no live-scan fallback: an unindexed document's concepts
  are simply absent until it is built (unlike `search`/graph reads, which scan
  `sections.jsonl` on miss).

## Fallback

Query tools preserve the existing **index-or-scan** behaviour: if
`indexes/bookgraph.db` is missing, or the requested `doc_id` has no row in
`doc_catalog`, the tool falls back to a live scan/build from that document's
`sections.jsonl`. Results stay correct; only ranking fidelity and latency differ
(the live-scan path uses a Unicode-aware, diacritic-folding term-frequency scorer,
which matches the same terms as FTS5 but will not rank byte-identically to `bm25`).

## Concurrency

- Readers open the database read-only (`mode=ro`) and never write.
- `index build` is the only writer; builds are per-document transactions, so
  building one document never corrupts another's rows.
