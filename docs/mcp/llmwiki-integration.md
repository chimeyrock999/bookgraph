# Running llmwiki MCP alongside BookGraph MCP

BookGraph ships an `llmwiki` **wiki backend** (`bookgraph wiki compile … --backend llmwiki`) that renders a BookGraph *wiki projection* under `wiki/books/<doc_id>/`. Separately, the standalone `llm-wiki-compiler` tool (`llmwiki`) is a **compiler** with its own project lifecycle and its own MCP server (`llmwiki serve`).

These are two different things that happen to share a name, and the distinction matters:

| | BookGraph wiki projection | Compiled llmwiki project |
|---|---|---|
| Produced by | `bookgraph wiki compile … --backend llmwiki` | `llmwiki compile` |
| Lives under | `wiki/books/<doc_id>/README.md`, `wiki/books/<doc_id>/sections/*.md` | `llmwiki/sources/*.md` → `llmwiki/wiki/concepts/`, `llmwiki/wiki/index.md`, `llmwiki/.llmwiki/state.json` |
| Queryable by `llmwiki serve`? | **No** — llmwiki does not adopt `wiki/books/**` as compiled pages | Yes |

A BookGraph wiki projection is *not* a compiled llmwiki project. Pointing `llmwiki serve` at a workspace that only has `wiki/books/**` reports `{"pages": {"total": 0}, "stateStatus": "missing"}` — there is nothing compiled to serve. To make llmwiki queryable you must **bridge** BookGraph sections into an llmwiki project and compile them (see below).

The compiled llmwiki project lives in its **own isolated `llmwiki/` subtree**, deliberately *not* the workspace root: llmwiki's generated `wiki/concepts/` and `wiki/index.md` would otherwise collide with BookGraph's own `wiki/` tree — which `bookgraph index concepts` deletes and rewrites unconditionally — silently wiping compiled llmwiki pages. Keeping llmwiki under `llmwiki/` guarantees the two never step on each other.

**Short answer:** run both — BookGraph MCP as the primary reading server, llmwiki MCP as an optional secondary server for compiled-wiki queries. Do **not** replace one with the other.

## Why BookGraph MCP stays primary

BookGraph MCP owns BookGraph's **canonical reading graph and stateful reading loop**. It reads only from:

- `sources/sections/<doc_id>/sections.jsonl` — the authoritative section text + provenance
- `indexes/bookgraph.db` — search (FTS5), the structural graph, and the cross-book concept graph
- `reading_plans/<plan_id>.json` — per-plan progress
- `annotations/<doc_id>/<section_id>.json` — Tier-2 agent annotations

and exposes the stateful, source-grounded tools that drive a reading session: `list_documents`, `create_plan`, `get_next_section`, `get_context`, `get_concept`, `get_related`, `get_outline`, `search`, `annotate_section`, `mark_read`, `list_plans` (see [`docs/cli/commands.md`](../cli/commands.md#bookgraph-mcp) and [`reading-agent.md`](reading-agent.md)).

**BookGraph MCP never reads the generated wiki pages and never depends on llmwiki being installed.** Its source of truth is `sections.jsonl` + `indexes/bookgraph.db` + `reading_plans/*.json`, and that boundary is deliberate.

## What llmwiki MCP is useful for

llmwiki MCP serves a **different layer**: the *compiled* wiki/RAG/query artifacts that `llm-wiki-compiler` produces — embeddings, concept pages, and retrieval-style tools such as `read_page`, `query_wiki`, `search_pages`, and `get_context_pack`.

Reach for it when you want compiled-wiki behaviour rather than source-grounded reading:

- semantic / embedding search across compiled pages (`query_wiki`, `search_pages`)
- reading a specific compiled wiki page (`read_page`)
- assembling a retrieval context pack for a downstream prompt (`get_context_pack`)

Treat its answers as derived-from-wiki, not as the reading graph's ground truth.

## Bridging BookGraph sections into a compiled llmwiki project

`llmwiki` compiles the `sources/*.md` files under its project root. Ingesting one full-book Markdown file as a single source is **lossy** — llmwiki truncates a large source (e.g. a 653k-char book was cut to 100k chars), silently excluding most of the book.

BookGraph avoids this with an **incremental, per-section bridge**: each section becomes its own bounded `llmwiki/sources/<section_id>.md` file, carrying BookGraph provenance in its frontmatter so compiled pages can trace back to `doc_id` / `section_id`.

```bash
# Stage every section of a document as individual llmwiki sources, then compile.
bookgraph llmwiki bridge /path/to/workspace <doc_id> --compile

# Compound with reading progress: stage only the sections read so far in a plan.
bookgraph llmwiki bridge /path/to/workspace <doc_id> --plan <plan_id> --compile

# Print the compile command instead of running it (llmwiki need not be installed).
bookgraph llmwiki bridge /path/to/workspace <doc_id> --compile --print
```

Properties of the bridge:

- **No full-book truncation** — one bounded source file per section.
- **Idempotent** — an unchanged section is left untouched on disk, so llmwiki's own incremental compile skips it and a daily batch is added without reprocessing the whole book.
- **Provenance preserved** — each staged file's frontmatter records `bookgraph_doc_id` and `bookgraph_section_id`.
- **Canonical state untouched** — the bridge only *writes* derived files into the isolated `llmwiki/` subtree; it never mutates BookGraph's canonical inputs and never touches BookGraph's own `wiki/` or `sources/` trees.

## Serving the compiled llmwiki project

The llmwiki project root is the workspace's `llmwiki/` subtree. Serve it with the real `llm-wiki-compiler` v1.1 contract — `llmwiki serve --root <project>` (there is **no** positional root argument):

```bash
bookgraph llmwiki serve /path/to/workspace          # runs `llmwiki serve --root /path/to/workspace/llmwiki`
bookgraph llmwiki serve /path/to/workspace --print  # just print the command, do not launch
```

The wrapper resolves the `llmwiki/` project root and emits the `--root` command with shell-safe quoting. With `--print` it emits the command without running anything (handy for pasting into a client config). When launching, it fails with an actionable message if the project has not been compiled yet (`.llmwiki/state.json` missing), or if `llmwiki` is not installed.

## Side-by-side MCP client config

Run both servers in one client config. BookGraph MCP is bound to the workspace root; llmwiki MCP serves the compiled llmwiki project in the workspace's `llmwiki/` subtree:

```json
{
  "mcpServers": {
    "bookgraph": {
      "command": "bookgraph",
      "args": ["mcp", "/path/to/workspace"]
    },
    "llmwiki": {
      "command": "llmwiki",
      "args": ["serve", "--root", "/path/to/workspace/llmwiki"]
    }
  }
}
```

Prerequisites for the llmwiki entry:

- `llmwiki` (the `llm-wiki-compiler` tool) must be installed and on the client's PATH. It is **optional** — BookGraph MCP does not depend on it.
- Sections must be bridged and compiled first: `bookgraph llmwiki bridge /path/to/workspace <doc_id> --compile`, so the compiled `llmwiki/wiki/` + `llmwiki/.llmwiki/state.json` exist.

If `llmwiki` is not on the client's PATH, use its absolute path (or the appropriate `uv run …` invocation) the same way you would for `bookgraph`.

## The boundary in one line

**BookGraph MCP = source-grounded reading graph + stateful reading loop (canonical).**
**llmwiki MCP = compiled-wiki search / query / page / context-pack (derived, optional).**

Generated wiki pages never become BookGraph's source of truth; canonical input remains `sources/sections/*.jsonl` + `indexes/bookgraph.db` + `reading_plans/*.json`.
