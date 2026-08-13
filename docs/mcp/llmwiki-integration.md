# Running llmwiki MCP alongside BookGraph MCP

BookGraph already ships an `llmwiki` wiki backend (`bookgraph wiki compile … --backend llmwiki`), and the standalone `llm-wiki-compiler` tool exposes its own MCP server via `llmwiki serve`. This raises a natural question: if llmwiki already has MCP tools, why not point your client at that server instead?

**Short answer:** they serve two different layers. Run both — BookGraph MCP as the primary reading server, llmwiki MCP as an optional secondary server for compiled-wiki queries. Do **not** replace one with the other.

## Why BookGraph MCP stays primary

BookGraph MCP owns BookGraph's **canonical reading graph and stateful reading loop**. It reads only from:

- `sources/sections/<doc_id>/sections.jsonl` — the authoritative section text + provenance
- `indexes/bookgraph.db` — search (FTS5), the structural graph, and the cross-book concept graph
- `reading_plans/<plan_id>.json` — per-plan progress
- `annotations/<doc_id>/<section_id>.json` — Tier-2 agent annotations

and exposes the stateful, source-grounded tools that drive a reading session: `list_documents`, `create_plan`, `get_next_section`, `get_context`, `get_concept`, `get_related`, `get_outline`, `search`, `annotate_section`, `mark_read`, `list_plans` (see [`docs/cli/commands.md`](../cli/commands.md#bookgraph-mcp) and [`reading-agent.md`](reading-agent.md)).

These are the workflows that need source-grounded reading: linear reading order, outline hierarchy, related sections, cross-book concept backlinks, and durable per-plan progress. **BookGraph MCP never reads the generated wiki pages** — its source of truth is `sections.jsonl` + `indexes/bookgraph.db` + `reading_plans/*.json`, and that boundary is deliberate.

## What llmwiki MCP is useful for

llmwiki MCP serves a **different layer**: the *compiled* wiki/RAG/query artifacts that `llm-wiki-compiler` produces over `wiki/` — embeddings, concept pages, and retrieval-style tools such as `read_page`, `query_wiki`, `search_pages`, and `get_context_pack`.

Reach for it when you want compiled-wiki behaviour rather than source-grounded reading:

- semantic / embedding search across compiled pages (`query_wiki`, `search_pages`)
- reading a specific compiled wiki page (`read_page`)
- assembling a retrieval context pack for a downstream prompt (`get_context_pack`)

Its input is the compiled `wiki/` tree, which is **generated output**, not BookGraph's canonical source. Treat its answers as derived-from-wiki, not as the reading graph's ground truth.

## Side-by-side MCP client config

Run both servers in one client config. BookGraph MCP is bound to the workspace root; llmwiki MCP is pointed at the workspace's compiled `wiki/` directory:

```json
{
  "mcpServers": {
    "bookgraph": {
      "command": "bookgraph",
      "args": ["mcp", "/path/to/workspace"]
    },
    "llmwiki": {
      "command": "llmwiki",
      "args": ["serve", "/path/to/workspace/wiki"]
    }
  }
}
```

Prerequisites for the llmwiki entry:

- `llmwiki` (the `llm-wiki-compiler` tool) must be installed and on the client's PATH. It is **optional** — BookGraph MCP does not depend on it.
- A wiki must be compiled first, e.g. `bookgraph wiki compile /path/to/workspace <doc_id> [--backend llmwiki]`, so `/path/to/workspace/wiki` exists.

If `llmwiki` is not on the client's PATH, use its absolute path (or the appropriate `uv run …` invocation) the same way you would for `bookgraph`.

## Optional helper: `bookgraph llmwiki serve`

Rather than remembering that the wiki lives under `<workspace>/wiki`, you can let BookGraph resolve it:

```bash
bookgraph llmwiki serve /path/to/workspace          # resolves <workspace>/wiki and runs `llmwiki serve`
bookgraph llmwiki serve /path/to/workspace --print  # just print the command, do not launch
```

This is a thin convenience wrapper: it resolves the workspace's `wiki/` directory and launches `llmwiki serve <wiki_dir>`. It is entirely optional and lives behind the existing llmwiki integration boundary — it does not make `llmwiki` a dependency of `bookgraph mcp`, and it changes nothing about BookGraph's canonical inputs. With `--print` it emits the command without running anything (handy for pasting into a client config), and it fails with an actionable message when `llmwiki` is not installed or the wiki has not been compiled yet.

## The boundary in one line

**BookGraph MCP = source-grounded reading graph + stateful reading loop (canonical).**
**llmwiki MCP = compiled-wiki search / query / page / context-pack (derived, optional).**

Generated wiki pages never become BookGraph's source of truth; canonical input remains `sections.jsonl` + `indexes/bookgraph.db` + `reading_plans/*.json`.
