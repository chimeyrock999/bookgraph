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

## Placeholder command contracts

The commands below expose the CLI interface and write placeholder request
artifacts under `runs/cli-placeholders/`. They intentionally do **not** call the
backend parser/segmenter/wiki/reading-plan implementations yet.

### `bookgraph parse-book`

**Status:** Implemented as CLI placeholder.

```bash
bookgraph parse-book /path/to/workspace <book_id>
bookgraph parse-book /path/to/workspace <book_id> --parser mineru-runner
bookgraph parse-book /path/to/workspace <book_id> --dry-run
```

Writes, unless `--dry-run`:

```text
runs/cli-placeholders/parse-book-<book_id>.json
```

Placeholder declares:

- input: `sources/inbox/<book_id>/book.json`
- future output: `sources/parsed/<book_id>/document.json`
- `backend_not_run: true`

### `bookgraph segment`

**Status:** Implemented as CLI placeholder.

```bash
bookgraph segment /path/to/workspace <doc_id>
bookgraph segment /path/to/workspace <doc_id> --segmenter heading
bookgraph segment /path/to/workspace <doc_id> --dry-run
```

Writes, unless `--dry-run`:

```text
runs/cli-placeholders/segment-<doc_id>.json
```

Placeholder declares:

- input: `sources/parsed/<doc_id>/document.json`
- future outputs: `sources/sections/<doc_id>/sections.jsonl`, section Markdown files
- `backend_not_run: true`

### `bookgraph wiki compile`

**Status:** Implemented as CLI placeholder.

```bash
bookgraph wiki compile /path/to/workspace <doc_id>
bookgraph wiki compile /path/to/workspace <doc_id> --backend llmwiki
bookgraph wiki compile /path/to/workspace <doc_id> --dry-run
```

Writes, unless `--dry-run`:

```text
runs/cli-placeholders/wiki-compile-<doc_id>.json
```

Placeholder declares:

- input: `sources/sections/<doc_id>/sections.jsonl`
- future output: `wiki/books/<doc_id>/`
- `backend_not_run: true`

### `bookgraph reading-plan create`

**Status:** Implemented as CLI placeholder.

```bash
bookgraph reading-plan create /path/to/workspace <doc_id>
bookgraph reading-plan create /path/to/workspace <doc_id> --plan-id ddia --daily-sections 1
bookgraph reading-plan create /path/to/workspace <doc_id> --dry-run
```

Writes, unless `--dry-run`:

```text
runs/cli-placeholders/reading-plan-create-<plan_id>.json
```

Placeholder declares:

- input: `sources/sections/<doc_id>/sections.jsonl`
- future output: `reading_plans/<plan_id>.json`
- `backend_not_run: true`

### `bookgraph reading-plan next`

**Status:** Implemented as CLI placeholder.

```bash
bookgraph reading-plan next /path/to/workspace <plan_id>
bookgraph reading-plan next /path/to/workspace <plan_id> --dry-run
```

Writes, unless `--dry-run`:

```text
runs/cli-placeholders/reading-plan-next-<plan_id>.json
```

### `bookgraph reading-plan mark-read`

**Status:** Implemented as CLI placeholder.

```bash
bookgraph reading-plan mark-read /path/to/workspace <plan_id>
bookgraph reading-plan mark-read /path/to/workspace <plan_id> --section-id <section_id>
bookgraph reading-plan mark-read /path/to/workspace <plan_id> --dry-run
```

Writes, unless `--dry-run`:

```text
runs/cli-placeholders/reading-plan-mark-read-<plan_id>.json
```

## Future backend contracts

The placeholder commands above reserve the interfaces. Do not wire real backend
execution into them without first expanding this file and the artifact contracts.
