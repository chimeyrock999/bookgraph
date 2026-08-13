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
```

### Inputs

- `workspace_path`: workspace/output root.
- `doc_id`: parsed document id under `sources/parsed/<doc_id>/`.
- `--segmenter/-s`: segmenter plugin name, validated against the segmenter registry. Default: `heading`.

Reads `sources/parsed/<doc_id>/document.json` (fails if missing).

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
- `--daily-sections`: sections per daily reading tick. Must be at least `1`. Default: `1`.
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

### Must not do (all reading-plan commands)

- Must not parse, segment, or compile wiki.
- Must not run MCP server/tools.
- Must not write outside `reading_plans/`.

## `bookgraph mcp`

**Status:** Implemented (requires the optional `mcp` extra).

Serve a workspace over MCP (stdio transport) so a reading client/agent can query
sections and drive a reading plan. All tools are read-mostly; only `mark_read`
mutates state (the reading plan).

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
- `search(query, doc_id=None, limit=10)` → sections ranked by query-term
  frequency in title and text, with a short snippet. `doc_id` scopes to one
  document; omit it to search every segmented document.

### Reads / writes

- Reads `sources/sections/<doc_id>/sections.jsonl` and `reading_plans/<plan_id>.json`.
- `mark_read` writes `reading_plans/<plan_id>.json` (same contract as
  `bookgraph reading-plan mark-read`). No other tool writes.

> `search` is a naive linear scan with no persistent index — enough to expose the
> tool contract. A real sections/graph index under `indexes/` is a planned
> follow-up.

## Placeholder command contracts

The commands below expose the CLI interface and write placeholder request
artifacts under `runs/cli-placeholders/`. They intentionally do **not** call the
backend parser/wiki implementations yet.

### `bookgraph parse-book`

**Status:** Implemented as CLI placeholder.

Declare the book-level raw PDF parse interface. This is aligned with the MinerU
runner contract from PR #1, but it still does **not** invoke MinerU or parse the
produced middle JSON.

```bash
bookgraph parse-book /path/to/workspace <book_id>
bookgraph parse-book /path/to/workspace <book_id> --runner mineru --method auto
bookgraph parse-book /path/to/workspace <book_id> --runner-command mineru --backend pipeline
bookgraph parse-book /path/to/workspace <book_id> --timeout-seconds 3600
bookgraph parse-book /path/to/workspace <book_id> --parser mineru-middle-json
bookgraph parse-book /path/to/workspace <book_id> --dry-run
```

Options:

- `--runner`: raw-source runner reserved for the future first step. Default: `mineru`.
- `--runner-command`: executable name reserved for the future runner. Default: `mineru`.
- `--method/-m`: MinerU method reserved for the future runner. Default: `auto`.
- `--backend/-b`: optional MinerU backend reserved for the future runner.
- `--timeout-seconds`: subprocess timeout reserved for the future runner. Default: `3600`; pass `0` to reserve no timeout.
- `--parser/-p`: parser reserved after runner output is staged. Default: `mineru-middle-json`.

`--parser` is validated against the parser plugin registry; typoed plugin names fail before a placeholder is written.

Writes, unless `--dry-run`:

```text
runs/cli-placeholders/parse-book-<book_id>.json
```

Placeholder declares:

- inputs: `sources/inbox/<book_id>/book.json`, `sources/inbox/<book_id>/original.<source_type>`
- future runner outputs staged flat under `sources/parsed/<book_id>/`:
  - `<book_id>_middle.json`
  - optional `<book_id>.md`
  - optional `<book_id>_layout.pdf`
  - optional `<book_id>_span.pdf`
  - optional `<book_id>_content_list.json`
  - optional `images/`
- future final output: `sources/parsed/<book_id>/document.json`
- `backend_not_run: true`

### `bookgraph wiki compile`

**Status:** Implemented as CLI placeholder.

```bash
bookgraph wiki compile /path/to/workspace <doc_id>
bookgraph wiki compile /path/to/workspace <doc_id> --backend llmwiki
bookgraph wiki compile /path/to/workspace <doc_id> --dry-run
```

`--backend` is validated against the wiki-backend plugin registry; typoed plugin names fail before a placeholder is written.

Writes, unless `--dry-run`:

```text
runs/cli-placeholders/wiki-compile-<doc_id>.json
```

Placeholder declares:

- input: `sources/sections/<doc_id>/sections.jsonl`
- future output: `wiki/books/<doc_id>/`
- `backend_not_run: true`

## Future backend contracts

The placeholder commands above reserve the interfaces. Do not wire real backend
execution into them without first expanding this file and the artifact contracts.
