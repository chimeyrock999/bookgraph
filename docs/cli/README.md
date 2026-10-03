# BookGraph CLI documentation

The `bookgraph` CLI builds and operates a source-grounded document workspace. It keeps
pipeline stages explicit: registration does not parse, parsing does not segment,
segmenting does not compile a wiki, and MCP reads from prepared artifacts instead of
running ingestion implicitly.

Use this directory for user-facing command guidance and stable CLI/artifact contracts.
Implementation rationale and test seams belong under `docs/design/`.

## Common workflow

```bash
bookgraph init /path/to/workspace
bookgraph add-book /path/to/workspace /path/to/book.pdf
bookgraph parse-book /path/to/workspace <book_id> --tier basic      # raw PDF via MinerU 4
# or: bookgraph parse notes.md -o /path/to/workspace                # Markdown/MarkItDown path
bookgraph segment /path/to/workspace <doc_id> --segmenter heading
bookgraph index build /path/to/workspace <doc_id>
bookgraph reading-plan create /path/to/workspace <doc_id> --plan-id main
bookgraph mcp /path/to/workspace
```

Optional human-facing outputs:

```bash
bookgraph wiki compile /path/to/workspace <doc_id>
bookgraph index concepts /path/to/workspace
bookgraph export translated-pdf /path/to/workspace <doc_id> --lang vi
bookgraph export translated-pdf /path/to/workspace <doc_id> --lang vi --out exports/book.vi.epub
```

## Command groups

Implemented command groups exposed by `bookgraph --help`:

| Command | Purpose |
| --- | --- |
| `init` | Create the workspace directory layout and `bookgraph.toml`. |
| `paths` | Print canonical workspace paths. |
| `add-book` | Register a PDF, EPUB, or DOCX source without parsing it. |
| `parse-book` | Run MinerU 4 on a registered raw source, then convert output to `document.json`. |
| `parsers` | List parser plugins. |
| `parse` | Parse a source file into canonical blocks under `sources/parsed/`. |
| `segment` | Convert parsed blocks into human reading sections under `sources/sections/`. |
| `index build` | Build SQLite FTS/search, section graph, and concept indexes. |
| `index concepts` | Render cross-book concept pages from the index. |
| `wiki compile` | Compile section manifests into a wiki backend output. |
| `reading-plan` | Create, list, inspect, and advance reading plans. |
| `mcp` | Serve one workspace over stdio MCP for reading/search/graph tools. |
| `concepts` | Manage concept aliases, canonical names, ignores, lint, and suggestions. |
| `llmwiki` | Optional llm-wiki-compiler bridge/view/query integration. |
| `export` | Build translated/bilingual reading editions as PDF, HTML, or EPUB. |
| `assets` | Repair/recover parsed image/table assets. |

## Contract files

- [`workspace.md`](workspace.md) — canonical workspace paths and naming rules.
- [`commands.md`](commands.md) — CLI command contracts, inputs, outputs, side effects,
  and error behavior.
- [`artifacts.md`](artifacts.md) — JSON/Markdown artifact schemas, reading plans,
  translations, export reports, and status transitions.
- [`annotations.md`](annotations.md) — per-section annotation artifacts and the
  annotation/index reinforcement loop.
- [`concepts.md`](concepts.md) — concept registry aliases, canonical concepts,
  ignores, merge suggestions, lint, and review.
- [`index.md`](index.md) — `indexes/bookgraph.db` schema, build, and query contract.
- [`parse-book-large-pdfs.md`](parse-book-large-pdfs.md) — long raw-PDF parse runtime,
  logs, model caches, and diagnosis.
- [`handoff.md`](handoff.md) — feature-branch and cross-agent integration workflow.

## Rules for agents and maintainers

- Treat files in `docs/cli/` as stable behavior, not casual notes.
- Prefer explicit workspace paths over implicit global state.
- Keep parser, segmenter, wiki, index, export, and MCP stages separate unless a command
  contract explicitly wires them together.
- Preserve provenance whenever transforming source content.
- Write deterministic filesystem artifacts under the workspace root.
- If implementation and contract disagree, update the relevant contract before changing
  code.
