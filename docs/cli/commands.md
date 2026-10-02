# CLI Command Contracts

Status terms:

- **Implemented**: behavior exists and has tests.
- **Contracted**: behavior is agreed here, implementation may follow.
- **Planned**: design sketch only; update contract before implementation.

## Global CLI behavior

- Commands must prefer explicit paths over implicit global state.
- Write commands must print the main artifact path they wrote.
- Failed commands must exit non-zero and include the offending path/plugin/name in the error text.
- Heavy external tools must not be invoked implicitly by registration commands.
- Parser/segmenter/wiki commands are separate stages unless a future orchestration command explicitly wires them together.

## `bookgraph init`

**Status:** Implemented.

Create a workspace skeleton.

```bash
bookgraph init /path/to/workspace
bookgraph init --output /path/to/workspace
bookgraph init -o /path/to/workspace
```

### Inputs

- `PATH` or `--output/-o`: workspace root. Exactly one is required.

### Writes

- Creates every directory listed in `workspace.md`.
- Writes `bookgraph.toml` if missing.
- Does not overwrite an existing `bookgraph.toml`.

### Prints

```text
Initialized BookGraph workspace at <absolute_workspace_path>
```

## `bookgraph paths`

**Status:** Implemented.

Print canonical paths for a workspace.

```bash
bookgraph paths /path/to/workspace
```

### Inputs

- `PATH`: workspace root.

### Writes

- None.

### Prints

One `name: absolute_path` line per canonical path. Required names:

```text
root
config
sources.inbox
sources.parsed
sources.sections
wiki.root
wiki.concepts
wiki.books
wiki.comparisons
wiki.daily
indexes.root
annotations.root
reading_plans.root
runs.root
```

## `bookgraph add-book`

**Status:** Implemented.

Register a raw PDF book without running parser, segmenter, wiki, or MCP stages.

```bash
bookgraph add-book /path/to/workspace /path/to/book.pdf
bookgraph add-book /path/to/workspace /path/to/book.pdf --dry-run
```

### Inputs

- `workspace_path`: workspace/output root.
- `pdf_path`: raw PDF source path.
- `--dry-run`: compute contract and print paths, but write nothing.

### Writes

For a book id `<book_id>` derived from the PDF filename:

```text
sources/inbox/<book_id>/
  original.pdf
  book.json
```

### Must not do

- Must not parse the PDF.
- Must not call MinerU, MarkItDown, segmenters, wiki backends, MCP tools, embeddings, or LLMs.
- Must not write under `sources/parsed/`, `sources/sections/`, `wiki/`, `indexes/`, or `reading_plans/`.

### Prints

Success:

```text
Registered book <book_id>
Manifest: <workspace>/sources/inbox/<book_id>/book.json
No parser or segmenter was run.
```

Dry run:

```text
Would register book <book_id>
Manifest: <workspace>/sources/inbox/<book_id>/book.json
No parser or segmenter was run.
```

### Known follow-up contract gaps

These are not required by the current implementation, but next CLI work should define and implement them:

- Missing PDF paths should fail cleanly before copy.
- Re-registering an existing `book_id` should have explicit `--overwrite` or `--id` behavior before destructive writes.
- Workspace validation should decide whether `bookgraph.toml` is required or auto-init is allowed.

## `bookgraph parsers`

**Status:** Implemented.

List parser plugin names.

```bash
bookgraph parsers
```

### Writes

- None.

### Prints

One parser plugin name per line, sorted by registry order/name contract. Current required names:

```text
markdown
markitdown
mineru-middle-json
```

## `bookgraph parse`

**Status:** Implemented.

Parse a source document into canonical blocks under `sources/parsed/<doc_id>/`.

```bash
bookgraph parse /path/to/source.md --output /path/to/workspace
bookgraph parse /path/to/source.md -o /path/to/workspace --parser markdown
bookgraph parse /path/to/book_middle.json -o /path/to/workspace
bookgraph parse /path/to/report.docx -o /path/to/workspace
bookgraph parse /path/to/source.md -o /path/to/workspace --doc-id custom-id
```

### Inputs

- `source`: source file to parse.
- `--output/-o`: workspace root. Defaults to current directory if omitted.
- `--parser/-p`: explicit parser plugin. If omitted, select by source type.
- `--doc-id`: override output `doc_id`.

### Parser routing

Current auto-routing:

| Source type | Parser |
| --- | --- |
| `*_middle.json` | `mineru-middle-json` |
| other `.json` files | fail unless `--parser` is explicit |
| `.md`, `.markdown`, `.mdx` | `markdown` |
| Office/HTML/text extensions supported in routing | `markitdown` |
| raw `.pdf` | fail unless `--parser` is explicit |

Reason for raw PDF failure: the current MinerU adapter consumes MinerU `*_middle.json`; it does not invoke MinerU from PDF. A future parser-runner command should own raw PDF execution.

### Writes

Always writes:

```text
sources/parsed/<doc_id>/document.json
```

Parser adapters may write side artifacts inside the same directory. Current examples:

- `markitdown` writes staged Markdown: `sources/parsed/<doc_id>/<doc_id>.md`.
- `markdown` writes no side artifact beyond `document.json`.
- `mineru-middle-json` writes no side artifact beyond `document.json`.

### Must not do

- Must not segment.
- Must not compile wiki.
- Must not update reading progress.
- Must not run MCP server/tools.

### Prints

```text
parser: <parser_name>
doc_id: <doc_id>
title: <document_title>
blocks: <block_count>
document: <workspace>/sources/parsed/<doc_id>/document.json
```

## `bookgraph segment`

**Status:** Implemented.

Segment a parsed document into human reading sections.

```bash
bookgraph segment /path/to/workspace <doc_id>
bookgraph segment /path/to/workspace <doc_id> --segmenter heading
bookgraph segment /path/to/workspace <doc_id> --segmenter bookmark
bookgraph segment /path/to/workspace <doc_id> --segmenter token-page
bookgraph segment /path/to/workspace <doc_id> --target-level 1
bookgraph segment /path/to/workspace <doc_id> --segmenter token-page --max-tokens 800
```

### Inputs

- `workspace_path`: workspace/output root.
- `doc_id`: parsed document id under `sources/parsed/<doc_id>/`.
- `--segmenter/-s`: segmenter plugin name, validated against the segmenter registry.
  Defaults to `[segmenter].default` in `bookgraph.toml` (`heading` when unset).
- `--target-level`: heading levels at or above this number start new sections for
  the built-in heading segmenter. For the `bookmark` segmenter, this selects the
  deepest PDF bookmark level that starts sections. Defaults to
  `[segmenter].target_level` in `bookgraph.toml` (`2` when unset). Other
  segmenters may ignore this option.
- `--max-tokens`: maximum token budget per `token-page` section. Defaults to
  `[segmenter].max_tokens` in `bookgraph.toml` (`800` when unset). Ignored by
  heading/bookmark segmenters.

Reads `sources/parsed/<doc_id>/document.json` (fails if missing). The `bookmark`
segmenter also reads `sources/inbox/<doc_id>/book.json` and uses its
`pdf.bookmarks` array when present; without usable bookmarks it falls back to the
heading segmenter. Bookmark sections come out in page order; bookmarks on the same
page keep their outline (TOC) order, and each section's `heading_path` is its
outline ancestry (`["Part I", "Chapter 1", "Storage"]`). The `token-page` segmenter is a deterministic fallback for
documents with weak/missing headings or bookmarks: it keeps blocks whole,
chunks by a token budget, and prefers page boundaries when a page break is
available near the budget.

### Writes

```text
sources/sections/<doc_id>/sections.jsonl
sources/sections/<doc_id>/<section_id>.md
sources/sections/<doc_id>/quality.json
```

See `artifacts.md` for the section artifact schemas. Duplicate section ids fail
the command rather than overwriting.

`quality.json` is the ingest data-quality report for the document: the anomalies
the deterministic checks in `bookgraph.quality` found in the sections just
written (inverted/half-known page ranges, assets whose caption contradicts the
parser's type, asset files the parser never staged, sections whose text is only
asset captions). It is always written
— `warning_count: 0` for a clean document — and the same warnings are attached to
every section the MCP section APIs return. See `artifacts.md` for the schema and
the code list.

### Must not do

- Must not parse.
- Must not compile wiki.
- Must not update reading progress.
- Must not run MCP server/tools.

### Prints

```text
segmenter: <segmenter_name>
target_level: <n>
max_tokens: <n>      # only printed for token-page
doc_id: <doc_id>
sections: <section_count>
manifest: <workspace>/sources/sections/<doc_id>/sections.jsonl
warnings: <warning_count>
quality: <workspace>/sources/sections/<doc_id>/quality.json
```

When the report is not empty, the command also echoes the per-code totals and up
to five `warning: <section_id>: <code>: <message>` lines (with an `… and N more`
line pointing at `quality.json`), so an anomaly is never hidden in a file the
operator did not open. Warnings do not fail the command — the sections are still
written.

## `bookgraph reading-plan`

**Status:** Implemented.

Build and advance daily reading progression state for one segmented document.
The plan is a single JSON file per plan id under `reading_plans/`; see
`artifacts.md` for its schema.

### `bookgraph reading-plan create`

Create a reading plan from a document's sections manifest.

```bash
bookgraph reading-plan create /path/to/workspace <doc_id>
bookgraph reading-plan create /path/to/workspace <doc_id> --plan-id ddia --daily-sections 1
bookgraph reading-plan create /path/to/workspace <doc_id> --dry-run
```

#### Inputs

- `workspace_path`: workspace/output root.
- `doc_id`: segmented document id under `sources/sections/<doc_id>/`.
- `--plan-id`: reading plan id; doubles as the output filename. Defaults to `doc_id`.
- `--daily-sections`: sections per daily reading tick. Must be at least `1`.
  Defaults to `[reading_plan].daily_sections` in `bookgraph.toml` (`1` when unset).
- `--dry-run`: compute and print the plan without writing files.

Reads `sources/sections/<doc_id>/sections.jsonl` (fails if missing or empty). The
manifest's line order is taken as the linear reading order.

#### Writes

```text
reading_plans/<plan_id>.json
```

#### Prints

```text
plan_id: <plan_id>
doc_id: <doc_id>
daily_sections: <n>
sections: <section_count>
reading_plan: <workspace>/reading_plans/<plan_id>.json
```

### `bookgraph reading-plan next`

Print the next unread sections without mutating the plan.

```bash
bookgraph reading-plan next /path/to/workspace <plan_id>
```

#### Inputs

- `workspace_path`: workspace/output root.
- `plan_id`: existing reading plan id (fails if the plan file is missing).

#### Writes

- None. `next` is read-only.

#### Prints

```text
plan_id: <plan_id>
doc_id: <doc_id>
next: <section_id>[, <section_id> ...]     # or "(complete)" when nothing is left
remaining: <unread_count>
```

`next` returns up to `daily_sections` unread section ids, in reading order.

### `bookgraph reading-plan progress`

Print progress within the chapter that holds the next unread section, without
mutating the plan.

```bash
bookgraph reading-plan progress /path/to/workspace <plan_id>
bookgraph reading-plan progress /path/to/workspace <plan_id> --chapter-level 2
```

#### Inputs

- `workspace_path`: workspace/output root.
- `plan_id`: existing reading plan id (fails if the plan file is missing).
- `--chapter-level`: heading level of the chapter scope. The chapter is the nearest
  ancestor-or-self of the next unread section whose `level` is at most this value
  (e.g. `2` for chapters nested under level-1 parts). Must be at least `1`.
  Defaults to the top-level ancestor; when the document has a single top-level
  heading (a `# Book Title` above every chapter, common for Markdown/EPUB), that
  lone root is skipped one level down so the scope is the chapter, not the whole
  book. Pass `--chapter-level` explicitly for part/chapter books or deeper wrappers.
  While a wrapper heading is itself the next unread section, the scope is that
  heading's own section only and the boundary is its first child, so a tick never
  spans a whole book or part. A wrapper is the lone root by default (day 1 of a
  fresh plan), or, with `--chapter-level`, any heading shallower than that level
  (e.g. a part heading with `--chapter-level 2`). A deeper section under a level jump
  (`part(1) > sec(3)` with `--chapter-level 2`) still belongs to the part's span.

#### Writes

- None. `progress` is read-only.

#### Prints

```text
plan_id: <plan_id>
doc_id: <doc_id>
completed: <completed_count>/<section_count>
chapter: <chapter_title> (<chapter_section_id>)     # or "(complete)" and stop
chapter_progress: <completed_in_chapter>/<total_in_chapter>
remaining_in_chapter: <unread_in_chapter>
next: <section_id>[, <section_id> ...]     # next daily batch, clipped at the boundary
next_boundary: <title> (<section_id>)      # or "(end of document)"
```

The hierarchy is derived from `sections.jsonl` exactly as `get_outline` derives it.
Counts are by **membership** in the chapter's subtree and in the plan, not by
position, so they stay correct after a plan reset, skipped front matter (sections
marked read up front), or out-of-order `mark-read` calls. Front-matter sections
that are their own top-level headings are their own "chapter" until read.

### `bookgraph reading-plan mark-read`

Mark a section read and persist the updated plan.

```bash
bookgraph reading-plan mark-read /path/to/workspace <plan_id>
bookgraph reading-plan mark-read /path/to/workspace <plan_id> --section-id <section_id>
bookgraph reading-plan mark-read /path/to/workspace <plan_id> --dry-run
```

#### Inputs

- `workspace_path`: workspace/output root.
- `plan_id`: existing reading plan id (fails if the plan file is missing).
- `--section-id`: specific section id to mark. Must belong to the plan. Defaults
  to the next unread section. Re-marking an already-read section is idempotent.
- `--dry-run`: print what would be marked without writing files.

#### Writes

```text
reading_plans/<plan_id>.json        # updated in place, unless --dry-run
```

#### Prints

```text
plan_id: <plan_id>
marked: <section_id>
completed: <completed_count>/<section_count>
reading_plan: <workspace>/reading_plans/<plan_id>.json
```

### `bookgraph reading-plan list`

List the workspace's reading plans with their completion progress.

```bash
bookgraph reading-plan list /path/to/workspace
```

#### Inputs

- `workspace_path`: workspace/output root.

#### Writes

- None. `list` is read-only.

#### Prints

```text
<plan_id>\t<doc_id>\t<completed>/<total>     # one line per plan, tab-separated
```

Prints `reading_plans: (none)` when the workspace has no plans. Unreadable/corrupt
plan files are skipped rather than aborting the listing.

### Must not do (all reading-plan commands)

- Must not parse, segment, or compile wiki.
- Must not run MCP server/tools.
- Must not write outside `reading_plans/`.

## `bookgraph index build`

**Status:** Implemented.

Build the persistent index that backs MCP query tools, deriving it per document
from the sections manifest into one workspace-wide SQLite database
(`indexes/bookgraph.db`):

- a **search** full-text index (FTS5 `sections_fts`) backing `search`;
- a **structural graph** (`section_graph`) — hierarchy + sequence edges — backing
  the graph/context tools;
- a **concept graph** (`concept_mentions`, aggregated by the `concept_nodes` view)
  — cross-book concept backlinks, populated per document and backing `get_concept`;
- a **`doc_catalog`** row marking each document as indexed.

See `docs/cli/index.md` for the full schema and query contract.

```bash
bookgraph index build /path/to/workspace                 # index every segmented document
bookgraph index build /path/to/workspace --doc-id ddia   # index one document
```

### Inputs

- `workspace_path`: workspace/output root. Must already exist (`bookgraph init`).
- `--doc-id`: index only this document. Validated as a filesystem-safe slug.
  Omit it to index every segmented document under `sources/sections/`.

### Reads

- `sources/sections/<doc_id>/sections.jsonl` (the sections to index).
- `annotations/<doc_id>/<section_id>.json` — Tier-2 agent annotations, merged with the
  deterministic Tier-1 extraction per the presence-based rule in `annotations.md`. The
  annotation files are a source of truth: `index build` reads them, never writes them.

### Writes

- `indexes/bookgraph.db` — the workspace-wide SQLite index. Each document is
  (re)built idempotently and atomically into `doc_catalog` + `sections_fts` +
  `section_graph` + `concept_mentions` (Tier-1/Tier-2 merged, carrying `gloss`/`source`)
  + `section_annotations` (per-section summaries), delete-then-insert per `doc_id`;
  other documents' rows are never touched. Fully regenerable from `sections.jsonl` +
  `annotations/` and safe to delete and rebuild (see `docs/cli/index.md`).

### Must not do

- Must not parse, segment, or compile wiki.
- Must not write outside `indexes/`. In particular it reads, but never writes,
  `annotations/`.

### Prints

- `doc_id` and `sections` per document, then the `backend` name and the `index`
  location (for the default backend, the `indexes/bookgraph.db` path) once.

### Errors

- No `--doc-id` and nothing segmented → `No segmented documents under …`.
- `--doc-id` given but its `sections.jsonl` is missing → `Sections manifest not found`.
- An invalid `concepts/registry.json` → `Invalid concept registry …` (the build
  refuses to guess rather than silently un-merge aliases).

## `bookgraph concepts`

**Status:** Implemented.

Curate the concept registry (`concepts/registry.json`) and report concept hygiene:
merge suggestions, lint, and the agent-concept review queue. Report commands
(`suggest`, `lint`, `review`) read the built index plus the registry. Write commands
(`alias`, `unalias`, `canonical`, `ignore`, `distinct`) edit only the registry and
take effect on the next `bookgraph index build`. See `docs/cli/concepts.md` for the
full contract.

```bash
bookgraph concepts suggest /path/to/workspace
bookgraph concepts alias /path/to/workspace metadata-file table-metadata
bookgraph index build /path/to/workspace
```

### Must not do

- Must not write the index, annotations, or wiki output. Only `concepts/registry.json`.

### Errors

- Report commands without a built index → `No concepts in … Run 'bookgraph index build' first.`
- An invalid registry, or a mutation that would break its invariants (an alias chain,
  ignoring a canonical slug, a non-slug id) → a `BadParameter` naming the conflict.

## `bookgraph index concepts`

**Status:** Planned (this contract).

Render the cross-book concept pages from the index. This is a **global** pass over
every indexed document (concept pages aggregate across books, so unlike `index
build` it is not per-document).

```bash
bookgraph index concepts /path/to/workspace
bookgraph index concepts /path/to/workspace --durable-only
```

### Inputs

- `workspace_path`: workspace/output root. Must already exist and have a built
  `indexes/bookgraph.db`.
- `--durable-only`: skip concepts with a lint **warning** (generic, one-off, stale
  alias/ignored; see `concepts.md`) unless they are canonical in
  `concepts/registry.json`. Prints `skipped: N`.

### Writes

- `wiki/concepts/<concept_slug>.md` — one page per concept, with cross-book
  backlinks, rendered from `concept_nodes` + `concept_mentions`. Each backlink shows
  its per-mention `gloss` when present and an `(agent-verified)` marker when the
  mention's `source` is `agent`. A concept in `concepts/registry.json` takes its
  canonical title, and a concept with aliases gets an `Also known as:` line. Rewrites the whole `wiki/concepts/` directory so it
  reflects exactly the currently indexed concepts (see `docs/cli/artifacts.md`).

> Backlinks point into `wiki/books/<doc_id>/sections/`, which is materialized by
> `bookgraph wiki compile <doc_id> --backend markdown-graph`, not by this command.
> Run `wiki compile` for each book so the links resolve; `index concepts` prints a
> `warning:` line counting any backlinks whose book page has not been compiled yet.

### Must not do

- Must not parse, segment, build the index, or compile `wiki/books/` — it owns only
  `wiki/concepts/`.
- Must not read wiki output; concepts come from the index (itself derived from
  `sections.jsonl`).

### Prints

- The number of concept pages written and the `wiki/concepts/` output directory.
- A `warning:` line when any backlink targets a `wiki/books/` section page that has
  not been compiled yet, pointing the user to `bookgraph wiki compile`.

### Errors

- No `indexes/bookgraph.db` / no indexed documents → an actionable message telling
  the user to run `index build` first.

## `bookgraph mcp`

**Status:** Implemented (requires the optional `mcp` extra).

Serve a workspace over MCP (stdio transport) so a reading client/agent can query
sections and drive a reading plan. All tools are read-mostly; only `mark_read`,
`complete_reading_batch`, and `create_plan` write reading-plan state,
`annotate_section` writes a Tier-2 annotation artifact, and
`write_section_translation` writes a cached translation.

```bash
uv sync --extra mcp
bookgraph mcp /path/to/workspace
```

### Inputs

- `workspace_path`: workspace/output root. Must already exist (`bookgraph init`).

The server binds to that one workspace; tool arguments never take a workspace
path. If the `mcp` extra is not installed, the command fails with a message
telling the user to `uv sync --extra mcp`.

### Server instructions

The server sends MCP `instructions` (`bookgraph.mcp.server.SERVER_INSTRUCTIONS`) to
every client at connect time, so the contract binds any agent, not only one that
loaded a `bookgraph-reader` skill:

- Artifacts hold book content only. Translated text goes in
  `write_section_translation(content=...)`, with figures/tables linked by
  `AssetRef.link` (relative). QA/terminology remarks go in `notes`. `MEDIA:` markers
  and progress lines go in the agent's final chat reply. Export status stays in the
  export report.
- The translation registry is the only translation store: check
  `get_section_translation` first, and save only with `write_section_translation`.
  Translation files written anywhere else (under `translations/` by hand, or in an
  agent's own directory such as `translation_cache/`) are never read.
- A job that translates or annotates finishes each batch with
  `complete_reading_batch`, not `mark_read`. A translation job always passes
  `translation_lang`; without it nothing checks that a translation was saved. A
  translation-only job also passes `require_annotation=False` and `index="ignore"`
  (or `"deferred"`), since the defaults require an annotation and a fresh index.

A test asserts these rules are present, so they cannot be dropped silently. The tool
docstrings (`write_section_translation`, `mark_read`) and both skills repeat them.

### Tools

- `get_next_section(plan_id, include_assets=True, stop_at_boundary=False,
  chapter_level=None)` → the next up-to-`daily_sections` unread sections for a
  plan, each shaped exactly as `get_section` returns it (full text, provenance,
  `<section_id>.md` path, `assets`, `warnings`), plus `remaining`, `done`, and
  `chapter` (as in `get_plan_progress`; `null` when done). `stop_at_boundary=True`
  clips the batch at the end of the current chapter so a tick never spills into the
  next one; `chapter_level` picks the chapter scope as below.
- `get_plan_progress(plan_id, chapter_level=None)` → a plan's progress without any
  section bodies: `completed`, `total`, `remaining`, `done`, `current_section_id`
  (the next unread section), `chapter` (`{section, completed, remaining, total,
  next_boundary}`, where `section` and `next_boundary` are id/title/level refs and
  `next_boundary` is `null` at the end of the document; `chapter` is `null` when the
  plan is done), and `next_sections` — the next `daily_sections` batch as refs,
  clipped at the boundary. The chapter is the top-level ancestor of the next unread
  section by default (a lone book-title root is skipped one level down), or the
  nearest ancestor-or-self with `level <= chapter_level`.
  Counts are by membership, so plan resets, skipped front matter, and out-of-order
  `mark_read` calls stay correct (same semantics as `bookgraph reading-plan progress`).
- `get_section(doc_id, section_id, include_assets=True)` → one section's full
  reading content, its `<section_id>.md` path, its figure/table `assets` (each
  `{block_id, type, path, link, caption, order, page_idx, type_confidence,
  suggested_type}`), and its `warnings`. `path` is the absolute file to open; `link`
  is the same file relative to `sources/parsed/<doc_id>/` (e.g. `images/fig1.png`),
  the reference to write into a translation. `type_confidence` scores the parser's
  classification against the caption's label and `suggested_type` is set only when
  the caption contradicts it, so a figure emitted as a `table` is flagged rather
  than passed off as correct. `warnings` are the same data-quality anomalies the
  segment stage records in `quality.json` (each `{code, message, block_id}`, see
  `artifacts.md`) restricted to this section: page-range warnings always, asset
  warnings when assets are included. `include_assets=False` skips asset resolution
  (and with it the asset warnings).
- `mark_read(plan_id, section_id=None)` → mark a section read (defaults to the
  next unread one) and persist the plan; returns `completed`/`total`/`done`.
- `validate_reading_batch(plan_id, section_ids=None, require_annotation=True,
  index="fresh", require_assets=True, inspected_assets=None, translation_lang=None,
  artifacts=None, stop_at_boundary=False, chapter_level=None)` → check whether a batch of sections is ready to be marked read,
  **without writing**. Returns the same report as `complete_reading_batch` (see
  below) with `committed: false`.
- `complete_reading_batch(...)` (same arguments) → the formal completion boundary
  for a reading batch: run every readiness check and, only when none blocks, mark
  the **whole** batch read in one atomic plan write. On any blocking issue the plan
  is untouched — progress advances by the entire batch or not at all. Use it instead
  of `mark_read` when a batch involves enrichment (annotation, translation, figure
  inspection, index rebuild). See *Reading batch completion* below.
- `search(query, doc_id=None, limit=10)` → sections ranked by FTS5 `bm25` over
  title and text, with a short snippet. `doc_id` scopes to one document; omit it
  to search across every indexed document (cross-document search), each hit
  carrying its `doc_id`.
- `get_outline(doc_id, root_id=None, max_depth=None)` → the document's section
  outline (heading hierarchy) in reading order: one node per section with `title`,
  `level`, `parent_id`, and `child_ids`. With no options it covers the whole
  document, which can be very large for a real book. `root_id` scopes it to that
  section's subtree (the section included). `max_depth` keeps that many **tree**
  levels from the scope's top (`1` = top-level sections only, or `root_id` alone).
  Depth follows the parent chain, not the heading `level`, so skipped levels don't
  matter. The result also carries `root_id`, `total_nodes` (the document's full
  section count), and `truncated` (whether `max_depth` cut deeper sections off).
  Boundary nodes keep their full `child_ids`, so a client can drill in with
  `root_id`. An unknown `root_id` or a `max_depth < 1` is an error. Note that on
  a flat document (page/token fallback, every section top-level) `max_depth=1`
  is still every section; `total_nodes` tells a client how large a level is.
- `get_section_tree(doc_id, section_id, include_siblings=True,
  include_children=True, sibling_window=10)` → a small outline around one
  section: `section`, `ancestors` (root-first breadcrumb), `siblings` (the
  parent's children in reading order, the section itself included; the
  top-level sections for a top-level section), and direct `children`. Each
  entry is an id/title/level reference. `siblings` keeps at most
  `sibling_window` entries on each side of the section (`null` = all), and
  `siblings_truncated` says whether any were dropped, so a flat document can't
  turn this into the whole book.
- `get_chapter_outline(plan_id, max_depth=2, chapter_level=None)` → the outline
  of the chapter a reading plan is currently in (`current_section_id` = the
  next unread section).
  - The chapter and its span come from the shared `graph.chapter_span`, so they
    always match `get_plan_progress`. By default the chapter is that section's
    outermost ancestor-or-self, except that a lone top-level root (one
    `# Book Title` heading above every chapter) is skipped one level down. With
    `chapter_level`, it is the nearest ancestor-or-self whose heading `level` is
    at most `chapter_level` (e.g. `2` for chapters under level-1 parts).
  - The span is normally the chapter's whole subtree. The exception is a
    wrapper heading that is itself the next unread section: then the span is
    that heading's own section only, so a tick never spans a whole book or
    part. A wrapper is the lone root by default, or, with `chapter_level`, any
    heading shallower than that level. A deeper section under a level jump
    (`part(1) > sec(3)`) still belongs to the part's span.
  - Each node in the span carries a `read` flag. `nodes` contains only span
    members, so a wrapper being read comes back alone; its `child_ids` still
    point at its children.
  - `completed` / `remaining` / `total` count the plan's sections in the
    chapter's span, by membership, whatever `max_depth` is set to (a wrapper
    being read counts as 1). `plan_completed` / `plan_total` are plan-wide.
  - `max_depth` works as in `get_outline` and defaults to `2` (the chapter and
    its direct subsections), so a chapter that turns out to be the whole book
    stays small. Pass `null` for the full subtree.
  - When the plan is done, `chapter` is null, `nodes` is empty, and the chapter
    counts are zero.
- `get_related(doc_id, section_id)` → a section's structural neighbours in the
  graph: `parent`, `prev`, `next`, and `children` (each a lightweight
  id/title/level reference).
- `get_context(doc_id, section_id)` → a section's full reading content (as
  `get_section`), its graph neighbourhood (as `get_related`), its `concepts`
  (each with `slug`, `title`, cross-book `doc_count` / `mention_count`, and the
  per-mention `gloss` / `source`) so a reader can pivot into `get_concept`, and the
  section's `summary`. The `summary` is read directly from the annotation file
  (`annotations/<doc_id>/<section_id>.json`), so it reflects an `annotate_section`
  call immediately; concepts are empty for an unindexed document and pick up
  gloss/source after the next `index build`.
- `get_concept(concept)` → a cross-book concept lookup: the concept node (`slug`,
  `title`, `doc_count`, `mention_count`) plus its backlink mentions
  (`doc_id`, `section_id`, `title`, `gloss`, `source`) across every indexed book,
  grouped by document. Returns empty when the slug is unknown. Backed by
  `concept_nodes` / `concept_mentions`; no live-scan fallback (a document's concepts
  exist only once it is built). An alias slug from `concepts/registry.json` resolves
  to its canonical concept: the result also carries `aliases`, `canonical`, and
  `resolved_from`, and each mention carries its `raw_slug` (see `concepts.md`).
- `concept_hygiene(limit=20, threshold=0.5)` → a read-only concept-maintenance report:
  `merge_suggestions` (likely duplicates with a suggested canonical side),
  `lint` (generic / one-off / over-granular / stale concepts), and `review_queue`
  (agent-created concepts not yet canonical, aliased, or ignored), each capped at
  `limit`. Decisions are applied by a human with the `bookgraph concepts` CLI.
- `annotate_section(doc_id, section_id, concepts=[], summary="", model=None)` →
  write a Tier-2 annotation for one section: the agent's authoritative concept edge
  set (each `{slug?, title, gloss?}`; `slug` defaults to a slugified `title`, and an
  empty/`untitled` slug is rejected) plus a prose `summary`. Writes only
  `annotations/<doc_id>/<section_id>.json` (see `annotations.md`); it does **not**
  touch the index. `doc_id` is validated as a slug; `section_id` is validated by
  **membership** (it must exist in the document — real ids contain a dot, so they are
  not bare slugs), rejecting unknown ids and traversal values. The summary is visible
  via `get_context` immediately; the concept edges (and their prune of Tier-1 false
  positives) take effect on the next `bookgraph index build <doc_id>`. Returns the
  written `doc_id`, `section_id`, `concept_count`, and `path`.
- `get_section_translation(doc_id, section_id, lang, include_content=True)` → the
  section's cached translation and its freshness: `status` (`fresh` / `stale` /
  `untracked` / `missing`, see `artifacts.md`), `path`, `metadata_path`,
  `source_section_hash`, `current_section_hash`, `includes_assets` (verified against
  the body; `null` for an untracked body), `missing_assets` (the section's staged
  figures/tables the body does not link, each with `block_id`, `type`, `link`,
  `reference`, `caption`), `section_has_assets` (whether the section owns any
  figure/table block), `model`, `created_at`, `notes` (the writer's side-channel
  remarks), `content` (the body, unless `include_content=False`), and
  `structure_issues` (the link destinations, image paths, reference definitions, HTML
  anchors, and heading ids the body dropped or added relative to the section; see the
  structure rule in `artifacts.md`). A missing translation is a normal result, not an
  error.
- `write_section_translation(doc_id, section_id, lang, content, includes_assets=False,
  model=None, source_section_hash=None, notes=None)` → write
  `translations/<lang>/<doc_id>/<section_id>.md` and its registry sidecar,
  replacing any previous translation; returns the entry (status `fresh`, no
  `content`, with `structure_issues` for the body just written — the write is kept
  either way). Empty `content` is rejected. When `source_section_hash` is given and
  differs from the section's current hash the write is refused, so a translation of
  outdated content is never registered as fresh. `content` is the translated book
  content only, with figures/tables linked by their `AssetRef.link`;
  `includes_assets=True` for a body that does not link every staged figure/table is
  refused with the missing links (the sidecar stores the claim, the read tools report
  it verified against the body). `notes` (optional) is the side channel for
  QA/checker results and terminology decisions: stored in the registry sidecar,
  returned as `notes` by the read tools, never in the body (see *Artifact channels*
  in `artifacts.md`).
- `list_section_artifacts(doc_id=None, lang=None, type="translation")` → every
  cached translation (filtered by `doc_id` / `lang`) with its status, including
  `orphaned` ones whose section no longer exists; no bodies, but each entry carries
  its `structure_issues`. Only `type="translation"` exists.
- `list_documents()` → the workspace's segmented documents, each with `doc_id`,
  `title` (from the parsed `document.json`, falling back to `doc_id`), and
  `section_count`. Lets an agent discover what there is to read before picking a
  document.
- `create_plan(doc_id, plan_id=None, daily_sections=1, overwrite=False)` → create
  and persist a reading plan for a segmented document (`plan_id` defaults to
  `doc_id`). Returns `plan_id`, `doc_id`, `daily_sections`, `section_count`, and
  `path`. Fails if the document is not segmented, or — unlike `bookgraph
  reading-plan create` — if a plan with that `plan_id` already exists, so a
  re-calling agent cannot silently wipe an in-progress plan; pass `overwrite=True`
  to replace it.
- `list_plans()` → the workspace's reading plans, each with `plan_id`, `doc_id`,
  `completed`, `total`, and `done`. Lets an agent resume or track progress.

Together `list_documents` → `create_plan` → `get_next_section`/`get_context` →
`mark_read` (or `complete_reading_batch`) → `list_plans`/`get_plan_progress` let a
client drive a full reading session without any CLI step (see `docs/mcp/reading-agent.md`).

### Reading batch completion

`validate_reading_batch` / `complete_reading_batch` take a `plan_id`, an optional
`section_ids` list, and the requirements the batch must meet. When `section_ids` is
omitted the batch is the plan's current batch, resolved by the **same** resolver as
`get_next_section` for the same `stop_at_boundary` / `chapter_level`: an agent that
reads boundary-clipped batches (`get_next_section(stop_at_boundary=True)`) must pass
the same flags here, or the default batch would spill past the chapter boundary.
Explicit `section_ids` take precedence over both flags; duplicates are dropped and an
empty list is rejected. Each requirement applies to every section of the batch:

| Argument | Default | Check |
|---|---|---|
| `require_annotation` | `true` | `annotations/<doc_id>/<section_id>.json` exists and is valid (readable, and its payload names this document/section — the same files `index build` accepts). |
| `index` | `"fresh"` | The document is in the index **and** the index reflects each section's current annotation: the stored `summary`/`model`/`created_at` match the file, and — when the annotation asserts `concepts` — the section's indexed concept edges are exactly those, agent-sourced. An unannotated section is fresh when the index stores no annotation for it. `"fresh"` makes a missing/stale index blocking; `"deferred"` reports it without blocking (the next `index build`, e.g. the nightly maintenance pass, folds it in); `"ignore"` skips the check. |
| `require_assets` | `true` | Every figure/table of the section whose file resolves (the `assets` of `get_section`) is listed by `block_id` in `inspected_assets` — the caller's declaration that it opened/embedded it. |
| `inspected_assets` | `[]` | Block ids the caller inspected. |
| `translation_lang` | `null` | When set (a slug such as `vi`, `pt-br`; lowercased), the section's cached translation `translations/<lang>/<doc_id>/<section_id>.md` exists, is non-empty, and is not `stale` in the translation registry (see `artifacts.md`). An `untracked` body (no valid registry sidecar) passes with a warning. |
| `artifacts` | `[]` | Extra workspace-relative path templates that must exist and be non-empty per section. `{doc_id}`, `{section_id}`, `{plan_id}` expand; an absolute path, a `..` segment, an unknown field, or a path resolving outside the workspace is rejected as a request error. |

Both return a report:

```json
{
  "plan_id": "daily", "doc_id": "ddia", "section_ids": ["ddia.a", "ddia.b"],
  "ok": false, "committed": false, "index_rebuild_needed": true,
  "issues": [
    {"code": "annotation_missing", "section_id": "ddia.b", "blocking": true,
     "message": "No annotation for 'ddia.b'; call annotate_section first."}
  ],
  "completed": 4, "total": 120, "done": false
}
```

- `ok` — no blocking issue. `committed` — `complete_reading_batch` actually marked
  the batch read (always `false` from `validate_reading_batch`).
- `index_rebuild_needed` — the index is missing or stale, whatever the policy, so a
  `"deferred"` caller knows to schedule `bookgraph index build <workspace> <doc_id>`.
- `completed` / `total` / `done` — plan progress after the call.
- Every problem is reported in one pass (not just the first), so a caller can fix
  them all before retrying.

Issue codes (`blocking` unless noted):

- `section_not_in_plan` — a requested id is not in the plan.
- `section_missing` — the plan references a section the document's
  `sections.jsonl` no longer has (re-segmented); recreate the plan.
- `already_read` — non-blocking; marking it again is idempotent.
- `annotation_missing` — only when `require_annotation`.
- `annotation_invalid` — corrupt or misplaced annotation file; blocking only when
  `require_annotation`.
- `index_missing` (batch-wide, `section_id: null`) / `index_stale` — blocking only
  when `index="fresh"`.
- `asset_not_inspected` — a resolvable figure/table was not in `inspected_assets`.
- `asset_file_missing` — non-blocking; the parser never staged the file, so it
  cannot be inspected (same condition as the section warning of that name).
- `asset_unknown` — non-blocking; `inspected_assets` names a block that is not an
  asset of the batch (likely a typo).
- `translation_missing`, `artifact_missing` — the required file is absent or empty.
- `translation_stale` — the registry records a translation of an older version of
  the section; re-translate with `write_section_translation`.
- `translation_untracked` — non-blocking; the body has no registry record, so its
  freshness is unknown.
- `translation_structure_changed` — a fresh or untracked translation dropped, added,
  or rewrote a link destination, image path, reference definition, HTML anchor, or
  heading id of the section; the message lists the changes. Translate labels and
  prose only and rewrite it (see the structure rule in `artifacts.md`).

Request errors — unknown/invalid `plan_id`, an unsegmented document, an empty
`section_ids`, a plan that is already complete when `section_ids` is omitted, an
invalid `translation_lang`, artifact template, or `chapter_level` — raise a tool error instead of
returning a report.

### Reads / writes

- Reads `indexes/bookgraph.db` (when built) and
  `sources/sections/<doc_id>/sections.jsonl`, plus `reading_plans/<plan_id>.json`,
  `annotations/<doc_id>/<section_id>.json` (the latter for `get_context.summary`),
  and `sources/parsed/<doc_id>/document.json` (asset resolution for `assets`; the
  section `warnings` are recomputed from the section and its assets, never read
  back from `quality.json`).
- `mark_read`, `complete_reading_batch`, and `create_plan` write
  `reading_plans/<plan_id>.json` (same contracts as `bookgraph reading-plan
  mark-read` / `create`; the file is replaced atomically, so a crash never leaves a
  truncated plan); `annotate_section` writes
  `annotations/<doc_id>/<section_id>.json` (see `annotations.md`);
  `write_section_translation` writes `translations/<lang>/<doc_id>/<section_id>.md`
  + `.json` (see `artifacts.md`). No other tool writes.

MCP tool inputs are client-controlled, so `plan_id` and `doc_id` are validated as
filesystem-safe slugs before they are used as path components; a traversal value
(e.g. `../secret`) is rejected before any file is read or written. `section_id`
is only ever matched against loaded plan/section data, never used as a raw path.

> `search` uses the FTS5 index in `indexes/bookgraph.db` (built by `bookgraph
> index build`) for documents present in `doc_catalog`, and falls back to a live
> scan of `sections.jsonl` for documents not yet indexed. The fallback keeps the
> legacy term-frequency scorer, so ranking is not byte-identical to `bm25`, but
> results stay correct whether or not an index exists.
>
> The graph tools (`get_outline` / `get_related` / `get_context`) likewise read
> the `section_graph` table when the document is in `doc_catalog`, and rebuild the
> graph from `sections.jsonl` otherwise, so they work before `index build` has run.
> A document absent from `doc_catalog` is always treated as unindexed. See
> `docs/cli/index.md`.

## `bookgraph llmwiki bridge`

**Status:** Implemented.

Stages BookGraph sections into the workspace's isolated `llmwiki/` project so a
compiled llmwiki project can be built, served and browsed. Each section becomes its own
bounded `llmwiki/sources/<doc_id>/<section_id>.md` file carrying BookGraph provenance
(`bookgraph_doc_id`, `bookgraph_section_id`) — a large book is never routed
through one truncating full-book ingest. The `llmwiki/` subtree is isolated from
BookGraph's own `wiki/` and `sources/` trees. See
`docs/mcp/llmwiki-integration.md`.

```bash
bookgraph llmwiki bridge /path/to/workspace <doc_id>                     # stage all sections
bookgraph llmwiki bridge /path/to/workspace <doc_id> --plan <plan_id>    # only sections read so far
bookgraph llmwiki bridge /path/to/workspace <doc_id> --compile           # stage, then `llmwiki compile`
bookgraph llmwiki bridge /path/to/workspace <doc_id> --compile --print   # print the compile command
bookgraph llmwiki bridge /path/to/workspace <doc_id> --compile --review --lang vi \
  --instructions editorial.md --concurrency 3                            # llmwiki 1.4 compile options
```

Requires `llm-wiki-compiler` >= 1.4.0 for `--compile` (see
`docs/mcp/llmwiki-integration.md` for why).

### Inputs

- `workspace_path`: workspace/output root. Must already exist.
- `doc_id`: sectioned document id (must have `sources/sections/<doc_id>/sections.jsonl`).
- `--plan <plan_id>`: restrict staging to sections already read in this reading
  plan, so the compiled wiki compounds with reading progress. Omit to stage every
  section of the document.
- `--compile`: after staging, run `llmwiki compile` incrementally with
  `<workspace>/llmwiki` as the working directory. `llmwiki compile` has no
  `--root` option (only `llmwiki serve` does); it compiles the current directory.
- `--print`: with `--compile`, print the compile command instead of running it,
  as a shell-quoted `cd <workspace>/llmwiki && llmwiki compile`.
- `--review`: with `--compile`, pass `--review` so generated pages become review
  candidates under `llmwiki/.llmwiki/candidates/` (approve with
  `llmwiki review approve <id>`, or browse them in `bookgraph llmwiki view`).
- `--lang <code>`: with `--compile`, target language of the generated wiki pages
  (e.g. `vi` for a Vietnamese wiki of an English book).
- `--instructions <file>`: with `--compile`, a UTF-8 editorial instructions file
  (max 64 KiB) appended to llmwiki's built-in prompt. The path is resolved against
  the caller's working directory and passed to llmwiki as an absolute path.
- `--concurrency <n>`: with `--compile`, max concurrent LLM calls (`n >= 1`).

### Behavior

- Reads the canonical sections manifest and writes one staged source file per
  selected section, always as a regular file (llmwiki >= 1.3 skips symlinked
  sources).
- **Layout**: when the bridge creates the llmwiki project (no `llmwiki/sources/`
  and no `llmwiki/.llmwiki/config.json` yet), it writes
  `{"version": 1, "sources": {"recursive": true}}` to `.llmwiki/config.json` and
  stages `llmwiki/sources/<doc_id>/<section_id>.md`, so the viewer's Sources
  screen lists each section as `<doc_id>/<section_id>.md` and a book's sections
  sort together. An existing `config.json` is never rewritten;
  sources are nested only if it already sets `sources.recursive: true`. A project
  staged flat (`llmwiki/sources/<section_id>.md`) before this layout keeps the flat
  layout, so re-running the bridge never leaves a second copy of every section for
  llmwiki to compile. The `layout:` output line says which one was used.
- **Idempotent**: an unchanged section is left untouched on disk (stable mtime), so
  llmwiki's incremental compile skips it and a daily batch is added without
  reprocessing the whole book. Reports `staged` / `unchanged` counts.
- With `--plan` and no sections read yet, stages nothing and says so.

### Must not do

- Must not read or mutate BookGraph's canonical inputs beyond *reading* the
  sections manifest and reading plan; it only *writes* derived files into `llmwiki/`.

### Errors

- Missing workspace directory → `Workspace not found: … Run 'bookgraph init' first.`
- Missing sections manifest → an actionable message pointing at `bookgraph segment`.
- `--plan` for a plan that does not exist / is for a different document → an
  actionable message.
- `--print`, `--review`, `--lang`, `--instructions` or `--concurrency` without
  `--compile` → rejected before staging (they only apply to the compile step).
- `--instructions` pointing at a missing file → rejected before staging.
- `--compile` with `llmwiki` not installed (without `--print`) → an actionable message.

## `bookgraph llmwiki serve`

**Status:** Implemented.

Convenience wrapper that launches the **optional** `llmwiki` MCP server over the
workspace's compiled llmwiki project. It is a secondary integration — BookGraph
MCP (`bookgraph mcp`) remains the primary reading server, and this command does not
make `llmwiki` a dependency of `bookgraph mcp`. See
`docs/mcp/llmwiki-integration.md`.

```bash
bookgraph llmwiki serve /path/to/workspace
bookgraph llmwiki serve /path/to/workspace --print
```

### Inputs

- `workspace_path`: workspace/output root. Must already exist.
- `--print`: print the resolved `llmwiki serve --root <workspace>/llmwiki` command
  and exit without launching anything (does not require a compiled project).

### Behavior

- Runs `llmwiki serve --root <workspace>/llmwiki` — the real `llm-wiki-compiler`
  contract (`--root <project>`, no positional root) — forwarding its exit code.
  `serve` is the only llmwiki command that takes `--root`; `compile`, `status` and
  `view` work on the current directory (see `llmwiki bridge --compile` and
  `llmwiki view`).
- With `--print`, emits the command with shell-safe quoting instead of running it.

### Must not do

- Must not parse, segment, compile wiki, build the index, or update reading
  progress.
- Must not read or mutate BookGraph's canonical inputs (`sources/sections/`,
  `indexes/bookgraph.db`, `reading_plans/`, `annotations/`).

### Errors

- Missing workspace directory → `Workspace not found: … Run 'bookgraph init' first.`
- Project not compiled yet (`llmwiki/.llmwiki/state.json` missing, without `--print`)
  → an actionable message pointing at `bookgraph llmwiki bridge … --compile`.
- `llmwiki` not installed / not on PATH (without `--print`) → an actionable message
  telling the user to install `llm-wiki-compiler` or re-run with `--print`.

## `bookgraph llmwiki view`

**Status:** Implemented.

Opens the workspace's compiled llmwiki project in llmwiki's local, read-only web
viewer (`llmwiki view`, `llm-wiki-compiler` >= 1.4.0). This is BookGraph's wiki
UI: compiled concept pages with citation chips back to source line ranges,
sources listed as `<doc_id>/<section_id>.md`, the concept graph, health/citation checks, the
`--review` queue and full-text search. The reading loop stays in BookGraph MCP.
See `docs/mcp/llmwiki-integration.md`.

```bash
bookgraph llmwiki view /path/to/workspace                 # print the URL and keep serving
bookgraph llmwiki view /path/to/workspace --open          # also open the browser
bookgraph llmwiki view /path/to/workspace --port 8123
bookgraph llmwiki view /path/to/workspace --print         # print the command only
```

### Inputs

- `workspace_path`: workspace/output root. Must already exist.
- `--open`: open the viewer in the default browser after startup.
- `--port <n>`: port to bind (`1`–`65535`; default: an OS-assigned port).
- `--print`: print the resolved command and exit without launching anything (does
  not require a compiled project).

### Behavior

- Runs `llmwiki view [--port <n>] [--open]` with `<workspace>/llmwiki` as the
  working directory — `llmwiki view` has no `--root` option and serves the current
  directory — forwarding its exit code. The viewer binds to `127.0.0.1` only.
- With `--print`, emits the shell-quoted `cd <workspace>/llmwiki && llmwiki view …`.

### Must not do

- Must not compile, stage sources, or write anything; the viewer is read-only.
- Must not read or mutate BookGraph's canonical inputs.

### Errors

- Missing workspace directory → `Workspace not found: … Run 'bookgraph init' first.`
- Project not compiled yet (`llmwiki/.llmwiki/state.json` missing, without `--print`)
  → an actionable message pointing at `bookgraph llmwiki bridge … --compile`.
- `llmwiki` not installed / not on PATH (without `--print`) → an actionable message.

## `bookgraph export translated-pdf`

**Status:** Implemented.

Assemble a partially translated book into one reading edition. A section with a
translation artifact for `--lang` renders that artifact; any other section follows
`--fallback`. This produces a clean reading edition. It does not reproduce the
publisher's page layout.

**Structure.** Sections are arranged into the book's structure:

- When `sources/inbox/<doc_id>/book.json` carries a PDF outline (`pdf.bookmarks`), it
  is the canonical table of contents. Sections are matched to bookmarks by title
  (ignoring case, punctuation, and quote style). When both pages are known, the
  bookmark must point into the section's page span, give or take one page, so a
  heading the outline does not list never takes a same-titled bookmark from another
  chapter. A repeated title such as *Conclusion* goes to the bookmark on the nearest
  page. Matched sections take the
  bookmark's level as their depth and are put in outline order. A section no bookmark
  names stays right after the matched section before it, one level deeper.
- Otherwise, or when no section title matches, `sections.jsonl` order and
  `Section.level` are kept.

A section renders inside its parent (`<section>` elements nest, and the TOC nests the
same way), with headings at its depth. A **chapter** starts a new page: every
top-level section, and each child of a top-level section that is a *part*. A part is
recognised by its title (*Part I*, *Book 2*, *Volume III*), or by its shape: at least
two children in the outline (or the manifest, without one), each with children of
its own, and at most ~300 words of its own. Shape is decided once for the whole book:
it counts only when most top-level sections that have children share it, and never
for a section titled as a chapter (*Chapter 3*), so page breaks do not differ between
chapters of one book. A section whose bookmark sits directly under a bookmark titled
as a part is a chapter too. Other sections flow inside their chapter. This matters for PDFs: MinerU marks every title as level
1, so a heading-segmented PDF has a flat manifest that the outline restores.

Two reader-facing modes (`--mode`):

```text
translated = mixed edition: translation where available, --fallback otherwise
bilingual  = original | mixed   (side by side, one row per section)
```

```bash
bookgraph export translated-pdf /path/to/workspace ddia --lang vi
bookgraph export translated-pdf /path/to/workspace ddia --lang vi --fallback skip \
  --out exports/ddia.vi-progress.pdf
bookgraph export translated-pdf /path/to/workspace ddia --lang vi --mode bilingual \
  --out exports/ddia.en-vi.pdf
bookgraph export translated-pdf /path/to/workspace ddia --renderer html   # no PDF extra needed
bookgraph export translated-pdf /path/to/workspace ddia --check           # coverage + QA only
```

### Inputs

- `workspace_path`, `doc_id`: workspace root and a segmented document id (slug-validated).
- `--lang` (default `vi`): translation language code, normalised like the registry
  (lowercased, so `VI` and `pt-BR` work).
- `--mode translated|bilingual` (default `translated`):
  - `translated` renders each section once: its translation, or the `--fallback`
    result.
  - `bilingual` renders each section as a two-column row on a landscape page. The
    left column is always the original section (rebuilt the same way as an
    `original` fallback, under its source title). The right column is exactly what
    `translated` mode renders for that section, so it follows `--fallback` too.
    Each section's row sits inside its parent's `<section>`, like the sections in
    `translated` mode, and both columns put its heading at the outline depth.
    Rows align at section level: translations are free Markdown without block ids,
    so finer alignment is not attempted. Figures, tables and code appear in the
    column whose content carries them (the original's figures on the left, the
    translation's own image links and code on the right); an untranslated section
    shows the original in both columns. HTML anchors (`id`, and `name` on `<a>`) are
    kept only in the right column, so every id on the page is unique and in-page links
    from either column land in the reading edition. The left column is tagged
    `lang="und"` (no source language is stored), and only right-column headings feed
    the PDF outline.
- `--fallback original|skip|fail` (default `original`). This controls what happens to
  a section that has no translation (in `bilingual` mode, to its right column):
  - `original` renders the original section;
  - `skip` renders the title only;
  - `fail` exits `1` and lists every untranslated section. Nothing is written. A
    `stale` or `untracked` translation counts as translated (use `--strict` to refuse
    stale ones).
- `--out`: output file. A relative path is resolved under the workspace. The default
  is `exports/<doc_id>.<lang>-progress.pdf` (`exports/<doc_id>.<lang>-bilingual.pdf`
  with `--mode bilingual`), or `.html` with `--renderer html`.
- `--renderer auto|weasyprint|playwright|html` (default `auto`). `auto` writes HTML
  for a `.html`/`.htm` output. For any other output it uses the first installed PDF
  backend, trying `weasyprint` first and then `playwright`. Naming a backend that is
  not installed is an error.
- `--strict`: exit `1` without writing anything if any asset is missing, remote, or
  unsupported, a translation is `stale` (`translation_stale`), or a registered
  translation left out its section's figures/tables (`translation_missing_assets`,
  checked from the body, not the sidecar's `includes_assets`),
  or a translation changed its section's link targets, anchors, or paths
  (`translation_structure_changed`). An `untracked` translation only warns. In
  `bilingual` mode the left column counts too: a missing original asset of a
  **translated** section is refused, even though `translated` mode never shows it.
  The same workspace can therefore pass `--strict` in `translated` mode and fail it
  in `bilingual` mode. Those problems are source assets, not translation problems:
  their warnings carry `"column": "original"` in the report and print as
  `[original column] …`, and the strict error counts them apart. Run
  `--mode translated --strict` to check the reading edition alone.
- `--show-status`: debug view. Also print status metadata on the reading pages:
  coverage/doc_id/mode/fallback on the title page, TOC status markers (`(original)`,
  `(may be outdated)`, `(not tracked)`, `(skipped)`), the *Untranslated — original
  text* / *Not translated yet* / freshness notes, *Missing asset* placeholders, and
  in `bilingual` mode the legend's sentence on how untranslated rows read. Without it
  that metadata is only in the report and the CLI output, so a reading edition
  carries book content only (a `bilingual` page still names its two columns).
- `--check`: preflight only. Prints coverage and warnings and writes nothing. With
  `--strict`, it exits `1` whenever the real export would be refused.
- The `--out` suffix must match the renderer: `.pdf` for `weasyprint`/`playwright`,
  `.html`/`.htm` for `html`. A mismatch, or a suffix that `auto` cannot map to a
  format, exits `2`.

### Reads

- `sources/sections/<doc_id>/sections.jsonl`: the skeleton.
- `sources/inbox/<doc_id>/book.json`, when present: its PDF outline sets reading
  order and hierarchy (see *Structure* above).
- `sources/parsed/<doc_id>/document.json`, when present. Original sections are
  rebuilt from their `block_ids`, so figures, tables and equations stay next to the
  prose around them in the source. Without it, original sections render
  `Section.text` as Markdown.
- Translations, through the translation registry (`bookgraph.translations`) only:
  `translations/<lang>/<doc_id>/<section_id>.md` plus its `.json` sidecar, which
  decides the section's `fresh` / `stale` / `untracked` status (the sidecar is never
  rendered). Stale and untracked translations render with a warning (and a visible
  note under `--show-status`); see `artifacts.md`. `translation_cache/` is not read.

### Asset handling

All images are embedded as `data:` URIs, so the output is self-contained.

- Markdown image links and raw HTML `<img src>` tags are handled the same way. A link
  in an artifact is resolved against the artifact's own
  directory, then `sources/parsed/<doc_id>/images/`, then `sources/parsed/<doc_id>/`,
  then the workspace root. An absolute path is accepted only if it points inside the
  workspace.
- Original asset blocks use the shared `bookgraph.assets.resolve_asset_path` resolver.
- A link is rejected if it leaves the workspace (including through symlinks), is a
  remote URL, or is not a png/jpeg/gif/svg/webp file. A rejected link, or an asset
  block whose file is missing, is left out of the page and reported with its section,
  reference, and the file carrying it. A block's caption and the surrounding prose
  stay, and a paragraph that held nothing but the image is dropped. `--show-status`
  shows a *Missing asset* placeholder in its place. It does not crash the export
  unless `--strict` is set. To get the figure back, run `bookgraph assets repair`.
- The page carries a CSP that allows only `data:` images and inline styles, and both
  PDF backends refuse any URL that is not `data:`. Rendering never reads the network
  or arbitrary files, and scripts in artifact HTML never run.

### Writes

- The export file at `--out`. It is rendered to a temporary sibling file and then
  moved into place, so a failed render never leaves a truncated file.
- `<out stem>.report.json`, the export report (see `artifacts.md`).

### Must not do

- Must not parse, segment, translate, or modify sections, translations, or the index.
- Must not fetch remote assets.

### Prints

- `doc_id`, `lang`, `mode`, `fallback`, and `coverage: <translated>/<total> (<pct>%)`.
- One `warning: <code>: <section_id>: <message>` line per warning, followed by
  ` (in <source_path>)` when the warning names the file carrying the reference.
  In `bilingual` mode the message is prefixed with `[original column] ` when the
  warning's `column` is `original`.
- `renderer`, `export`, and `report` paths. With `--check` it prints
  `export: (check only, not written)` instead.

### Errors

- Missing sections manifest → `Sections manifest not found` (exit 2).
- Unknown `--mode` / `--fallback` / `--renderer`, an invalid `--lang`, or an `--out` suffix that
  does not match the renderer → exit 2.
- `--fallback fail` with untranslated sections (including empty or unreadable
  artifacts; stale and untracked translations count as translated), `--strict` with
  asset problems or stale / prose-only translations (also under `--check`), or a
  missing or failing renderer → exit 1, with nothing written.

### PDF backends (optional extras)

| Renderer | Install |
|----------|---------|
| `weasyprint` | `uv sync --extra pdf` (also needs the Pango system library, e.g. `brew install pango`) |
| `playwright` | `uv sync --extra pdf-chromium && uv run playwright install chromium` |
| `html` | built in |

## `bookgraph assets repair`

**Status:** Implemented.

Recover image/table files that a parsed document references but never staged (the
`asset_file_missing` quality warning, `asset_missing` in an export).

```bash
bookgraph assets repair /path/to/workspace ddia --dry-run
bookgraph assets repair /path/to/workspace ddia
bookgraph assets repair /path/to/workspace ddia --from ~/ddia-figures
```

### Inputs

- `workspace_path`, `doc_id`: workspace root and a parsed document id (slug-validated).
- `--from DIR` (repeatable): extra directories to search after the parser output.
- `--dry-run`: report what would be recovered and write nothing.
- `--json`: print the repair report as JSON instead of lines.

### Behavior

Every asset block whose reference does not resolve (through the shared
`bookgraph.assets.resolve_asset_path`) is looked up, in order, in:

1. the parser output: any file under `sources/parsed/<doc_id>/` whose path ends with
   the reference, else whose basename matches it (hidden directories and symlinks
   are skipped);
2. the source EPUB (`document.metadata.source_path`), when there is one, matched like
   the parse-time EPUB stager matches members;
3. each `--from` directory, matched like the parser output.

The first source with exactly one match wins. Byte-identical copies count as one
match. When several different files match, the asset is reported as `ambiguous` and
left alone, because guessing could show the wrong figure. A recovered file is copied
into `sources/parsed/<doc_id>/images/` under a link-safe, non-clashing name, and its
block is repointed there (see *Repaired asset blocks* in `artifacts.md`). Remote and
absolute references are not repair candidates.

### Writes

- `sources/parsed/<doc_id>/images/<name>` for each recovered file.
- `sources/parsed/<doc_id>/document.json`, atomically, only when something was
  recovered.
- `sources/sections/<doc_id>/quality.json`, re-checked against the repaired document,
  when a sections manifest exists and something was recovered.

### Must not do

- Must not change block text, ids, or order, sections, or translation artifacts.
- Must not rewrite a reference it could not recover. The export already leaves
  such an asset out and keeps its caption.
- Must not fetch remote assets.

### Prints

- `doc_id`, `missing: <n>`, `recovered: <n>`.
- One line per missing asset: `recovered: <block_id> [<section_ids>]: <reference> ->
  images/<name> (from <source>)`, `ambiguous: … <n> candidate files, none used: …`,
  or `unrecoverable: <block_id> [<section_ids>]: <reference>`.
- `quality: <path> (refreshed)` when the quality report was rewritten, and
  `repair: (dry run, nothing written)` with `--dry-run`.

### Errors

- Exit `2` when `document.json` is missing, the sections manifest is invalid, or a
  `--from` path is not a directory. An unrecoverable asset is not an error.

## Book-level parse / wiki compile contracts

### `bookgraph parse-book`

**Status:** Implemented.

Run the registered-book parse pipeline: invoke the configured raw-source runner,
stage its outputs under `sources/parsed/<book_id>/`, then parse the staged MinerU
middle JSON into the canonical `document.json`.

```bash
bookgraph parse-book /path/to/workspace <book_id>
# Fast local text extraction for digital PDFs / weak GPU / Apple Silicon / CPU
bookgraph parse-book /path/to/workspace <book_id> --profile fast-text
# Explicit knobs (each overrides the profile)
bookgraph parse-book /path/to/workspace <book_id> \
  --backend pipeline --method txt --no-formula --no-table --no-image-analysis
# Accurate local GPU/VLM profile for machines with enough VRAM
bookgraph parse-book /path/to/workspace <book_id> --profile local-gpu --effort high
# Remote GPU / service-backed backend
bookgraph parse-book /path/to/workspace <book_id> \
  --backend hybrid-http-client --url http://gpu-box:30000
# Page range (0-based) and timeout
bookgraph parse-book /path/to/workspace <book_id> --start-page 0 --end-page 63
bookgraph parse-book /path/to/workspace <book_id> --timeout-seconds 3600
bookgraph parse-book /path/to/workspace <book_id> --parser mineru-middle-json --dry-run
```

Options:

- `--runner`: raw-source runner. Default: `[mineru].runner` (`mineru`).
- `--runner-command`: executable name. Default: `[mineru].command` (`mineru`).
- `--profile` / `--mineru-profile`: named MinerU profile picking hardware/quality
  defaults. One of `fast-text | balanced | accurate | local-gpu | remote-gpu`.
  Default: `[mineru].profile` (`balanced`). Explicit knobs below override it.
- `--method/-m` / `--mineru-method`: MinerU method `auto | txt | ocr`. Default: profile / `[mineru].method`.
- `--backend/-b` / `--mineru-backend`: MinerU backend
  `pipeline | vlm-engine | hybrid-engine | vlm-http-client | hybrid-http-client`. Default: profile / `[mineru].backend`.
- `--effort` / `--mineru-effort`: `medium | high`. Default: profile / `[mineru].effort`.
- `--formula/--no-formula`, `--table/--no-table`, `--image-analysis/--no-image-analysis`:
  toggle MinerU feature passes. Default: profile / `[mineru].*`.
- `--url/-u` / `--mineru-url`: remote GPU server URL, required by the `*-http-client` backends. Default: `[mineru].url`.
- `--start-page/-s`, `--end-page/-e`: 0-based page range. Default: `[mineru].start_page` / `end_page`.
- `--timeout-seconds`: subprocess timeout. Default: config; pass `0` for no timeout.
- `--parser/-p`: parser after runner output is staged. Default: `[parsers].default_pdf` (`mineru-middle-json`).

Precedence is CLI flag > workspace config (`[mineru]`) > profile default. The resolved
profile and the exact MinerU argv are recorded in the run log (the `$ mineru …` line).

`--parser` is validated against the parser plugin registry; typoed plugin names fail before the runner is invoked.
A `*-http-client` backend without a `--url` is rejected before MinerU is invoked.

**Choosing a profile:**

| Situation | Profile | Why |
| --- | --- | --- |
| Text-heavy digital book; reading sections / search matter more than layout | `fast-text` | `pipeline` + `txt`, table/formula/image off — skips the slow layout/VLM/OCR passes. |
| Default, mixed content | `balanced` | MinerU's stock medium-effort path (unchanged behavior). |
| Scanned / image-heavy PDF needing good tables & layout | `accurate` | Hybrid/VLM at `--effort high`. |
| Local machine with enough CUDA/VRAM | `local-gpu` | Pure local `vlm-engine` backend, high effort. |
| External GPU server | `remote-gpu` (+ `--url`) | `hybrid-http-client` offloads to the server. |
| Apple Silicon / CPU | `fast-text` | No CUDA-like speed is promised; prefer the fast text path unless accuracy needs VLM. |

Workspace defaults live under `[mineru]` in `bookgraph.toml`:

```toml
[mineru]
profile = "balanced"
# method = "auto"
# backend = "pipeline"
# effort = "high"
# formula = true
# table = true
# image_analysis = true
# url = ""
# start_page = 0
# end_page = 0
# timeout_seconds = 3600
```

Writes:

```text
sources/parsed/<book_id>/
  document.json
  <book_id>_middle.json
  optional <book_id>.md / *_layout.pdf / *_span.pdf / *_content_list.json / images/
```

Dry run still writes a placeholder request artifact under:

```text
runs/cli-placeholders/parse-book-<book_id>.json
```

Prints at run start:

```text
runner: <runner>
book_id: <book_id>
profile: <resolved_profile>
pages: <page_count_if_known>
log: <workspace>/runs/parse-book/<timestamp>-<book_id>.log
stage: running MinerU
```

Prints after success:

```text
parser: <parser_name>
doc_id: <book_id>
title: <document_title>
blocks: <block_count>
document: <workspace>/sources/parsed/<book_id>/document.json
```

Failures include `Log: <...>` so users/agents can inspect the durable run log.
For large PDFs and agent/cron operation, see `docs/cli/parse-book-large-pdfs.md`.

### `bookgraph wiki compile`

**Status:** Implemented.

```bash
bookgraph wiki compile /path/to/workspace <doc_id>
bookgraph wiki compile /path/to/workspace <doc_id> --backend llmwiki
bookgraph wiki compile /path/to/workspace <doc_id> --backend markdown-graph
bookgraph wiki compile /path/to/workspace <doc_id> --dry-run
```

`--backend` is validated against the wiki-backend plugin registry; typoed plugin names fail before sections are read.

Reads:

```text
sources/sections/<doc_id>/sections.jsonl
```

Writes:

```text
wiki/books/<doc_id>/
```

For `--backend markdown-graph`, section pages also include deterministic concept
wikilinks in a `## Linked concepts` block. It does not materialize or reconcile
`wiki/concepts/*.md`; cross-book concept nodes/backlinks belong to the index layer.

Dry run still writes a placeholder request artifact under:

```text
runs/cli-placeholders/wiki-compile-<doc_id>.json
```

Prints:

```text
backend: <backend_name>
doc_id: <doc_id>
sections: <section_count>
wiki: <workspace>/wiki/books/<doc_id>
```
