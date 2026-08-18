# CLI Artifact Contracts

This file defines filesystem artifact schemas shared by CLI stages.

## `sources/inbox/<book_id>/book.json`

Owner: `bookgraph add-book` and future book-level orchestration commands.

Current schema:

```json
{
  "book_id": "designing-data-intensive-applications",
  "title": "Designing Data Intensive Applications",
  "source_type": "pdf",
  "source_path": "/absolute/original/input/path/book.pdf",
  "workspace_path": "/absolute/workspace/path",
  "status": "registered",
  "pdf": {
    "title": "Designing Data-Intensive Applications",
    "author": "Martin Kleppmann",
    "pages": 616,
    "has_bookmarks": true,
    "bookmarks": [
      {"title": "Chapter 1. Reliable, Scalable, and Maintainable Applications", "page_index": 1, "level": 1}
    ]
  },
  "pipeline": {
    "parser": null,
    "segmenter": null,
    "wiki_backend": null
  },
  "paths": {
    "book_root": "/absolute/workspace/sources/inbox/<book_id>",
    "original": "/absolute/workspace/sources/inbox/<book_id>/original.pdf",
    "parsed": "/absolute/workspace/sources/parsed/<book_id>",
    "sections": "/absolute/workspace/sources/sections/<book_id>",
    "wiki": "/absolute/workspace/wiki/books/<book_id>"
  }
}
```

### Field rules

- `book_id`: stable slug derived from title/path unless explicitly overridden by a future option.
- `title`: human title derived from filename unless explicitly overridden by a future option.
- `source_type`: currently `pdf` only for `add-book`.
- `source_path`: absolute path to the user-provided source path at registration time.
- `workspace_path`: absolute workspace path used at registration time.
- `status`: current book-level lifecycle state.
- `pdf`: best-effort PDF metadata read at registration time. If optional `pypdf`
  support is unavailable or the source cannot be inspected, values fall back to
  `title: null`, `author: null`, `pages: 0`, `has_bookmarks: false`, and an
  empty `bookmarks` list. Bookmark `page_index` is zero-based when known and
  `level` preserves nested outline depth.
- `pipeline`: per-stage selected plugin/status placeholder. Current registration sets all values to `null`.
- `paths`: absolute target paths for downstream stages.

### Status values

Current implemented value:

- `registered`: raw source copied/registered; no parser/segmenter/wiki stage has run.

Future values must be added here before implementation, likely:

- `parsed`
- `segmented`
- `wiki_built`
- `failed`

## `sources/inbox/<book_id>/original.pdf`

Owner: `bookgraph add-book`.

Rules:

- Byte copy of the registered PDF source.
- Must keep `.pdf` extension.
- Must not be modified by parser/segmenter/wiki commands.

## `sources/parsed/<doc_id>/document.json`

Owner: `bookgraph parse` and future parser commands.

Current schema mirrors `bookgraph.models.Document`:

```json
{
  "doc_id": "deep-work",
  "title": "Deep Work",
  "blocks": [
    {
      "id": "b0",
      "type": "title",
      "text": "Deep Work",
      "level": 1,
      "page_idx": null,
      "bbox": null,
      "source_path": "/absolute/source/path.md",
      "order": 0,
      "metadata": {
        "line_start": 1,
        "line_end": 1
      }
    }
  ],
  "metadata": {
    "parser": "markdown",
    "source_path": "/absolute/source/path.md"
  }
}
```

### Document field rules

- `doc_id`: stable slug used as output folder name.
- `title`: parser-derived title if available; fallback to source filename/title.
- `blocks`: ordered canonical content blocks.
- `metadata.parser`: parser plugin name that produced the document.
- `metadata.source_path`: absolute source path parsed by the command.

### Block field rules

- `id`: stable within the document. Current adapters use order/page-based ids.
- `type`: one of canonical block types from `models.py`: `title`, `text`, `list`, `table`, `image`, `chart`, `equation`, `unknown`.
- `text`: normalized text content for downstream segmenters.
- `level`: heading/title level if known.
- `page_idx`: page index if known from paged parser output.
- `bbox`: source bounding box if known from layout parser output.
- `asset_path`: for `image`/`table`/`chart` blocks, the parser-relative filename of the
  extracted asset (e.g. MinerU's `fig1.jpg`, staged under `sources/parsed/<doc_id>/images/`).
  `null` for text blocks. Surfaced per section by the MCP `get_section` / `get_context`
  `assets` list (path, type, caption, order) so readers need not grep this file.
- `source_path`: path to source/parser artifact that proves the block.
- `order`: zero-based reading order.
- `metadata`: parser-specific provenance. Must be JSON scalar values only.

### Provenance rules for converting adapters

When an adapter converts the original source into Markdown before building
blocks, block-level and document-level provenance point at different files:

- block `source_path` is the **staged artifact the block was read from**, because
  `metadata.line_start` / `line_end` are line numbers in that artifact. Line 3 of
  a `.docx` has no meaning; line 3 of the staged Markdown does.
- `document.metadata.source_path` stays the **original user-provided source**.
- `document.metadata.markdown_path` records the staged Markdown artifact.

Current behavior:

| Parser | block `source_path` | `metadata.source_path` |
| --- | --- | --- |
| `markdown` | the `.md` source itself | same `.md` source |
| `markitdown` | `sources/parsed/<doc_id>/<doc_id>.md` | original `.docx`/`.pptx`/… |
| `mineru-middle-json` | the `*_middle.json` file | same `*_middle.json` file |

### List block rules

`list` blocks keep reading structure rather than flattening to bullets:

- ordered lists keep their numbers, including a non-default `start`;
- nested lists keep hierarchy as two-space indentation per depth level.

## Parser side artifacts

Parser commands may write side artifacts only under `sources/parsed/<doc_id>/`.

Allowed examples:

```text
sources/parsed/<doc_id>/<doc_id>.md                  # staged markdown from MarkItDown or MinerU
sources/parsed/<doc_id>/assets/...                   # extracted images/assets
sources/parsed/<doc_id>/<doc_id>_middle.json         # staged MinerU middle JSON
sources/parsed/<doc_id>/<doc_id>_layout.pdf          # MinerU layout debug PDF
sources/parsed/<doc_id>/<doc_id>_span.pdf            # MinerU span debug PDF
sources/parsed/<doc_id>/<doc_id>_content_list.json   # MinerU content-list JSON
sources/parsed/<doc_id>/images/...                   # MinerU extracted images
```

MinerU runner staging contract, once wired by a future backend command:

- `MinerURunner.run(original_pdf, sources/parsed/<doc_id>)` invokes the MinerU CLI.
- It stages artifacts flat under `sources/parsed/<doc_id>/` using `<doc_id>` as the filename stem.
- It does not produce `document.json`; `mineru-middle-json` remains the parser that turns `<doc_id>_middle.json` into canonical blocks.

If a parser writes side artifacts, it should reference them from `document.metadata` when useful.

## Placeholder request artifacts

Owner: CLI interface commands that reserve a future backend operation without
running it.

Path:

```text
runs/cli-placeholders/<command>-<id>.json
```

Common schema:

```json
{
  "command": "parse-book",
  "status": "placeholder",
  "book_id": "deep-work",
  "runner": {
    "name": "mineru",
    "command": "mineru",
    "method": "auto",
    "backend": null,
    "timeout_seconds": 3600
  },
  "parser": "mineru-middle-json",
  "inputs": {},
  "intermediate_outputs": {},
  "outputs": {},
  "backend_not_run": true
}
```

Rules:

- Placeholder artifacts are coordination contracts, not completed stage outputs.
- They must not be written into `sources/parsed`, `sources/sections`, `wiki`, or
  `reading_plans`.
- `backend_not_run` must be `true`.
- Backend agents can use these files to see the agreed command inputs/outputs.

## `sources/sections/<doc_id>/sections.jsonl`

Owner: the segment stage (`bookgraph segment` command / `bookgraph.sections.write_sections`).

Canonical machine-readable section manifest. One JSON object per line, each
mirroring `bookgraph.models.Section`:

```json
{"id": "ddia.chapter-3-storage", "doc_id": "ddia", "title": "Chapter 3. Storage", "level": 1, "heading_path": ["Chapter 3. Storage"], "page_start": 10, "page_end": 11, "text": "Opening paragraph.", "prev_id": null, "next_id": "ddia.sstables-and-lsm-trees", "block_ids": ["b1", "b2"], "metadata": {}}
```

### Section field rules

- `id`: `<doc_id>.<slug>` derived from the section title. Doubles as the
  `<section_id>.md` filename, so it must be unique within a document; the writer
  refuses duplicate ids rather than overwriting.
- `doc_id`: parent document id; matches the `sources/parsed/<doc_id>/` folder.
- `heading_path`: heading ancestry from the document root to this section.
- `page_start` / `page_end`: page span if known from paged parser output.
- `prev_id` / `next_id`: linear reading-order neighbours, `null` at the ends.
- `block_ids`: provenance back to `document.json` block ids that prove the section.

## `sources/sections/<doc_id>/<section_id>.md`

Owner: the segment stage (`bookgraph.sections.write_sections`).

Human-readable reading unit: YAML frontmatter carrying the same provenance
fields (`id`, `doc_id`, `title`, `level`, `heading_path`, `page_start`,
`page_end`, `prev_id`, `next_id`, `block_ids`) followed by the section heading
and text. Frontmatter values are emitted as JSON scalars/arrays (valid YAML) so
titles with colons or quotes cannot corrupt the frontmatter.

> Note: `sources/sections/` is owned by the segment stage. Wiki backends should
> read this manifest and emit compiled output under `wiki/`, not rewrite section
> source artifacts.

## `wiki/books/<doc_id>/`

Owner: the wiki stage (`bookgraph wiki compile` command / wiki backend plugins).

The `llmwiki` backend writes a book-local README plus section Markdown under
`wiki/books/<doc_id>/sections/`. The `markdown-graph` backend writes the same book
surface and adds deterministic concept wikilinks to section pages.

Required book output shape:

```text
wiki/books/<doc_id>/
  README.md
  sections/
    <section_id>.md
```

For the `markdown-graph` backend, section pages include a `## Linked concepts`
block with wiki-style links:

```text
- [[schema-evolution|Schema Evolution]]
```

The backend is intentionally stateless and book-local. It does **not** materialize
or reconcile `wiki/concepts/<concept_slug>.md`, does not store hidden backlink
state in Markdown, and does not own cross-book concept joins. Cross-book concept
nodes/mentions/backlinks belong to the index/query layer: the `concept_mentions`
table and `concept_nodes` view in `indexes/bookgraph.db`, from which the
`wiki/concepts/<concept_slug>.md` pages below are rendered by `bookgraph index
concepts` (see `index.md` for the schema and `commands.md` for the command).

Concept extraction is intentionally local and deterministic: no LLMs, embeddings,
or external services. It uses section titles, heading paths, title-case phrases,
and long domain-looking terms from a document's section text only. This extractor
lives in a shared module (`bookgraph.concepts`: `extract_concepts` + `ConceptEntry`)
used by **both** the `markdown-graph` wiki backend (for in-page wikilinks) and the
index stage (for `concept_mentions`), so the same slug/title is produced on both
sides. `sections.jsonl` is the single input; neither side reads the other's output.

## `wiki/concepts/<concept_slug>.md`

Owner: the index stage (`bookgraph index concepts` command / `bookgraph.index`).

One Markdown page per distinct concept, aggregated **across every indexed book**
and rendered from `indexes/bookgraph.db` (`concept_nodes` + `concept_mentions`).
This is the cross-book counterpart to the in-page `## Linked concepts` wikilinks
the `markdown-graph` backend emits: those link *to* `[[<slug>|Title]]`; this page
*is* that target and lists the backlinks.

```text
wiki/concepts/
  <concept_slug>.md      # e.g. schema-evolution.md
```

Page shape — a title, a one-line summary, then backlinks grouped by book in
reading order. A backlink shows its per-mention `gloss` after an em dash when present,
and an `(agent-verified)` marker when the mention came from a Tier-2 agent annotation
(`source='agent'`):

```markdown
# Schema Evolution

Mentioned in 2 books · 5 sections.

## Deep Work
- [Storage](../books/deep-work/sections/deep-work.a.md)

## Designing Data-Intensive Applications
- [Encoding and Evolution](../books/ddia/sections/ddia.ch-4.md) — why it matters here (agent-verified)
```

Properties:

- **Derived and fully rebuildable** — never a source of truth. `bookgraph index
  concepts` rewrites the whole `wiki/concepts/` directory from the database, so it
  reflects exactly the concepts of the currently indexed documents.
- **Cross-book, so not per-document**: unlike `index build <doc_id>` (which is
  per-doc), concept pages need every book's mentions, so they are (re)rendered by
  the separate global `index concepts` pass, run after the relevant books are
  built. Concept *data* (`concept_mentions`) is still populated per-doc by `index
  build`; only the page rendering is global.
- `<concept_slug>` is the deterministic slug from `bookgraph.concepts`
  (`[a-z0-9]+(?:-[a-z0-9]+)*`), matching the `[[<slug>|…]]` targets in book pages.

## `indexes/bookgraph.db`

Owner: the index stage (`bookgraph index build` command / `bookgraph.indexes`).

The search index and the structural graph are compiled into **one workspace-wide
SQLite database** (`sections_fts` FTS5 + `section_graph` + `doc_catalog`), not
per-document JSON. It is a derived, fully rebuildable artifact — the canonical
source of truth stays in `sources/sections/<doc_id>/`. See **`index.md`** for the
full schema, build, query, and fallback contract.

> Supersedes the earlier `indexes/sections/<doc_id>.json` (inverted index) and
> `indexes/graph/<doc_id>.json` (structural graph) files, which are removed once a
> document is built into the database.

## `reading_plans/<plan_id>.json`

Owner: the reading-plan stage (`bookgraph reading-plan` commands /
`bookgraph.reading_plans`).

Daily reading progression state for one document. One JSON file per plan id,
mirroring `bookgraph.models.ReadingPlan`:

```json
{
  "plan_id": "daily-ddia",
  "doc_id": "ddia",
  "daily_sections": 2,
  "section_ids": ["ddia.intro", "ddia.chapter-1", "ddia.chapter-2"],
  "completed": ["ddia.intro"]
}
```

### Field rules

- `plan_id`: reading plan id; doubles as the filename, so it is a filesystem-safe
  slug (lowercase a-z, 0-9, hyphens). Validated on create.
- `doc_id`: the segmented document this plan reads.
- `daily_sections`: sections returned per `reading-plan next` tick. At least `1`.
- `section_ids`: the document's sections in linear reading order, copied from the
  `sources/sections/<doc_id>/sections.jsonl` line order at create time.
- `completed`: section ids marked read, in the order they were completed. A subset
  of `section_ids`.

### Derived state (not stored)

- **next batch**: the first up-to-`daily_sections` ids in `section_ids` that are
  not in `completed`, in reading order.
- **done**: every id in `section_ids` is in `completed`.

These are recomputed from `section_ids` + `completed` on each `next` call rather
than persisted, so the file stays a minimal source of truth.

## `annotations/<doc_id>/<section_id>.json`

Owner: the MCP `annotate_section` tool (`bookgraph.mcp.service` /
`bookgraph.annotations`). Read by the index stage (`bookgraph index build`).

A **Tier-2 source of truth** — an agent's authoritative concepts + summary for one
section. Unlike `indexes/bookgraph.db` (derived), it is **not** rebuildable and ranks
alongside `sources/sections/<doc_id>/sections.jsonl`; `index build` reads it and never
writes it. One file per annotated section, mirroring
`bookgraph.models.SectionAnnotation`:

```json
{
  "doc_id": "ddia",
  "section_id": "ddia.schema-evolution",
  "concepts": [
    {"slug": "schema-evolution", "title": "Schema Evolution", "gloss": "why it matters here"}
  ],
  "summary": "the agent's explanation of this section",
  "model": "claude-...",
  "created_at": "2026-08-13T00:00:00Z"
}
```

The concept edges here are the authoritative set for that section and, on the next
`index build`, override the deterministic Tier-1 extraction (an empty `concepts` list
prunes that section's mentions). See **`annotations.md`** for the field rules, the
presence-based merge rule, and the `markdown-graph` non-goal.

## Future artifacts

Do not implement these without updating this file.
