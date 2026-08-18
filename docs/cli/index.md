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
    gloss         TEXT NOT NULL DEFAULT '',      -- per-mention note (Tier-2 only)
    source        TEXT NOT NULL DEFAULT 'auto',  -- 'auto' | 'agent'
    PRIMARY KEY (doc_id, section_id, concept_slug)
);
CREATE INDEX concept_mentions_slug ON concept_mentions (concept_slug);
```

- Concepts for a section come from **one of two tiers**, chosen per section by the
  presence of a Tier-2 annotation (see the `annotations.md` merge rule):
  - **Tier 1 (auto):** the shared deterministic extractor
    `bookgraph.concepts.extract_concepts` (the same one the `markdown-graph` wiki
    backend uses for in-page wikilinks), so an auto section's mentions match the
    `[[<slug>|Title]]` links on its book page. These rows carry `source='auto'` and an
    empty `gloss`.
  - **Tier 2 (agent):** an agent annotation
    (`annotations/<doc_id>/<section_id>.json`) is the authoritative edge set for that
    section. These rows carry `source='agent'` and the annotation's per-mention `gloss`.
    An annotation with an empty concept list **zeroes out** that section's rows (the
    false-positive prune) — see `annotations.md`.
- `concept_slug`: `[a-z0-9]+(?:-[a-z0-9]+)*`; the join key across books.
- `concept_title`: display title carried for labels. The same slug may carry
  slightly different titles across documents; a single canonical title is chosen
  at query time (see `concept_nodes`).
- `section_id`: the mentioning section, joinable to `section_graph` and the
  section files. `(doc_id, section_id)` scope makes per-doc delete trivial.
- `gloss`: a short per-mention note on why the concept matters in this section.
  Non-empty only for `source='agent'` rows; surfaced on concept pages and in
  `get_context.concepts`.
- `source`: `'auto'` or `'agent'`. Because concepts for a `(doc_id, section_id)` come
  from exactly one tier, all rows for a given pair share one `source`; the
  `PRIMARY KEY (doc_id, section_id, concept_slug)` makes a cross-tier collision for the
  same pair impossible.

### `section_annotations`

Per-section Tier-2 metadata that is **not** a concept edge: the agent's `summary` and
provenance. One row per annotated section. It is populated by `index build` from the
annotation files (mirroring `concept_mentions`' per-doc delete/insert) so the built
database carries the summary alongside the graph — but it is **not** a
`_REQUIRED_TABLES` member, so a database predating it still reads cleanly (its absence
just means "no stored summaries").

```sql
CREATE TABLE section_annotations (
    doc_id     TEXT NOT NULL,
    section_id TEXT NOT NULL,
    summary    TEXT NOT NULL DEFAULT '',
    model      TEXT,
    created_at TEXT,
    PRIMARY KEY (doc_id, section_id)
);
```

- `summary` / `model` / `created_at`: carried straight from the annotation file.
- `get_context` does **not** depend on this table for immediacy: it reads the single
  annotation file directly so a just-written summary shows before any rebuild. The table
  exists so the summary is queryable from the built index too.

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
3. Reads the document's Tier-2 annotations
   (`annotations/<doc_id>/<section_id>.json`, see `annotations.md`) and merges them
   with the deterministic Tier-1 extraction per the **presence-based merge rule**: for
   each section, an annotation (if present) is the authoritative concept edge set
   (`source='agent'`, carrying gloss — an empty concept list prunes the section);
   otherwise the auto extractor supplies the edges (`source='auto'`).
4. Rebuilds that document **idempotently and atomically** in one transaction:
   `DELETE FROM doc_catalog / sections_fts / section_graph / concept_mentions /
   section_annotations WHERE doc_id = ?`, then re-inserts the fresh rows (the merged
   concept mentions and the per-section summaries). Re-running build for the same
   document is a no-op net of content/annotation changes; other documents' rows are
   never touched.
5. Prints the database path and the section count written.

Rebuild a document after re-segmenting it **or after annotating any of its sections**
so the denormalised `title` / `text`, the graph edges, the concept mentions (Tier-1 +
Tier-2 merge), and the stored summaries stay in sync with `sections.jsonl` +
`annotations/`.

### Schema migration (pre-annotation databases)

The `gloss` / `source` columns on `concept_mentions` and the `section_annotations`
table were added for the Tier-2 enrichment. `index build` runs an idempotent,
guarded migration on open: it reads `PRAGMA table_info(concept_mentions)` and issues
`ALTER TABLE … ADD COLUMN` only for columns that are missing (both with `NOT NULL
DEFAULT` so existing rows backfill), and `CREATE TABLE IF NOT EXISTS section_annotations`.

- A pre-change database never crashes: its `concept_mentions` lacks the `gloss` /
  `source` columns, so the concept-read SELECTs (which now name those columns) hit
  "no such column" and the read-only wrapper degrades that read to **empty** — the same
  graceful "not indexed" path used for a corrupt/partial database. So on an un-rebuilt
  old database `get_concept` / `get_context.concepts` return empty rather than raising.
  (Reads never migrate — only `index build` writes.)
- **Migration note:** to restore concept reads and pick up gloss/source + stored
  summaries, run `bookgraph index build <doc_id>` once per document — that is what adds
  the columns and re-populates the rows. Until a document is rebuilt its concept reads
  are empty (never a crash); search and the graph tools are unaffected.

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
  `concept_mentions`, each with its cross-book `doc_count` / `mention_count` plus the
  per-mention `gloss` / `source`) so a reader can pivot from the current section into
  `get_concept`; these are empty for an unindexed document (concepts have no live-scan
  fallback). `get_context` also returns the section's `summary`, read **directly from
  the annotation file** (`annotations/<doc_id>/<section_id>.json`) rather than from the
  index, so a summary written by `annotate_section` shows before any `index build`.
- **`get_concept(concept, include_annotations=False)`**: looks a concept up by slug
  in `concept_nodes`, then reads its `concept_mentions` across **every** indexed book.
  Returns the node (`slug`, `title`, `doc_count`, `mention_count`) plus its backlink
  mentions (`doc_id`, `section_id`, section `title`, `gloss`, `source`) grouped/ordered
  by document — the cross-book "what mentions this concept" query. Two read modes share
  this shape: the default **compact card** for lightweight graph traversal, and the
  **detail view** (`include_annotations=True`), which joins `section_annotations` so
  each mention also carries its section's Tier-2 `summary`. In detail mode a concept
  with several mentions reads as a source-grounded note (each summary stays tied to its
  section); `annotated_mention_count` reports how many mentions carry one. Returns
  nothing when the slug is absent. Concepts have no live-scan fallback: an unindexed
  document's concepts are simply absent until it is built (unlike `search`/graph reads,
  which scan `sections.jsonl` on miss).

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
