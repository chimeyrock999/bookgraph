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
heading segmenter. The `token-page` segmenter is a deterministic fallback for
documents with weak/missing headings or bookmarks: it keeps blocks whole,
chunks by a token budget, and prefers page boundaries when a page break is
available near the budget.

### Writes

```text
sources/sections/<doc_id>/sections.jsonl
sources/sections/<doc_id>/<section_id>.md
```

See `artifacts.md` for the section artifact schemas. Duplicate section ids fail
the command rather than overwriting.

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
```

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

## `bookgraph index concepts`

**Status:** Planned (this contract).

Render the cross-book concept pages from the index. This is a **global** pass over
every indexed document (concept pages aggregate across books, so unlike `index
build` it is not per-document).

```bash
bookgraph index concepts /path/to/workspace
```

### Inputs

- `workspace_path`: workspace/output root. Must already exist and have a built
  `indexes/bookgraph.db`.

### Writes

- `wiki/concepts/<concept_slug>.md` — one page per concept, with cross-book
  backlinks, rendered from `concept_nodes` + `concept_mentions`. Each backlink shows
  its per-mention `gloss` when present and an `(agent-verified)` marker when the
  mention's `source` is `agent`. Rewrites the whole `wiki/concepts/` directory so it
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
sections and drive a reading plan. All tools are read-mostly; only `mark_read` and
`create_plan` write reading-plan state, and `annotate_section` writes a Tier-2
annotation artifact.

```bash
uv sync --extra mcp
bookgraph mcp /path/to/workspace
```

### Inputs

- `workspace_path`: workspace/output root. Must already exist (`bookgraph init`).

The server binds to that one workspace; tool arguments never take a workspace
path. If the `mcp` extra is not installed, the command fails with a message
telling the user to `uv sync --extra mcp`.

### Tools

- `get_next_section(plan_id)` → the next up-to-`daily_sections` unread sections
  for a plan, each with full text, provenance, and its `<section_id>.md` path,
  plus `remaining` and `done`.
- `get_section(doc_id, section_id)` → one section's full reading content.
- `mark_read(plan_id, section_id=None)` → mark a section read (defaults to the
  next unread one) and persist the plan; returns `completed`/`total`/`done`.
- `search(query, doc_id=None, limit=10)` → sections ranked by FTS5 `bm25` over
  title and text, with a short snippet. `doc_id` scopes to one document; omit it
  to search across every indexed document (cross-document search), each hit
  carrying its `doc_id`.
- `get_outline(doc_id)` → the document's section outline (heading hierarchy) in
  reading order: one node per section with `title`, `level`, `parent_id`, and
  `child_ids`.
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
  exist only once it is built).
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
`mark_read` → `list_plans` let a client drive a full reading session without any
CLI step (see `docs/mcp/reading-agent.md`).

### Reads / writes

- Reads `indexes/bookgraph.db` (when built) and
  `sources/sections/<doc_id>/sections.jsonl`, plus `reading_plans/<plan_id>.json` and
  `annotations/<doc_id>/<section_id>.json` (the latter for `get_context.summary`).
- `mark_read` and `create_plan` write `reading_plans/<plan_id>.json` (same
  contracts as `bookgraph reading-plan mark-read` / `create`); `annotate_section`
  writes `annotations/<doc_id>/<section_id>.json` (see `annotations.md`). No other
  tool writes.

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
compiled llmwiki project can be built and served. Each section becomes its own
bounded `llmwiki/sources/<section_id>.md` file carrying BookGraph provenance
(`bookgraph_doc_id`, `bookgraph_section_id`) — a large book is never routed
through one truncating full-book ingest. The `llmwiki/` subtree is isolated from
BookGraph's own `wiki/` and `sources/` trees. See
`docs/mcp/llmwiki-integration.md`.

```bash
bookgraph llmwiki bridge /path/to/workspace <doc_id>                     # stage all sections
bookgraph llmwiki bridge /path/to/workspace <doc_id> --plan <plan_id>    # only sections read so far
bookgraph llmwiki bridge /path/to/workspace <doc_id> --compile           # stage, then `llmwiki compile`
bookgraph llmwiki bridge /path/to/workspace <doc_id> --compile --print   # print the compile command
```

### Inputs

- `workspace_path`: workspace/output root. Must already exist.
- `doc_id`: sectioned document id (must have `sources/sections/<doc_id>/sections.jsonl`).
- `--plan <plan_id>`: restrict staging to sections already read in this reading
  plan, so the compiled wiki compounds with reading progress. Omit to stage every
  section of the document.
- `--compile`: after staging, run `llmwiki compile --root <workspace>/llmwiki`
  incrementally.
- `--print`: with `--compile`, print the compile command instead of running it.

### Behavior

- Reads the canonical sections manifest and writes one staged
  `llmwiki/sources/<section_id>.md` per selected section.
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
- `--print` without `--compile` → rejected (the flag only applies to the compile step).
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
  v1.1 contract (`--root <project>`, no positional root) — forwarding its exit code.
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
| Local machine with enough CUDA/VRAM | `local-gpu` | Local VLM/hybrid backend, high effort. |
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
