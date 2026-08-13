# Integrating a reading agent over MCP

This guide wires an AI agent (any MCP client — Claude Code, Claude Desktop, or a
custom client) to a BookGraph workspace so it can read a document section by
section, follow the concept graph, and track progress — with **no CLI step**
between "workspace prepared" and "agent reading".

The MCP server (`bookgraph mcp`) serves one workspace over stdio and exposes only
the tools below; it never reads the wiki files — it serves from
`sources/sections/` + `indexes/bookgraph.db` (see `.docs/cli/index.md`).

## 1. Prepare a workspace (one-time, per corpus)

```bash
uv sync --extra mcp

bookgraph init /path/to/ws
# ingest each document (Markdown/Office via `parse`; raw PDF via add-book + parse-book)
bookgraph parse book.md -o /path/to/ws            # → sources/parsed/<doc_id>/document.json
bookgraph segment /path/to/ws <doc_id>            # → sources/sections/<doc_id>/…
bookgraph index build /path/to/ws                 # → indexes/bookgraph.db (search + graph + concepts)

# optional, human-facing wiki (not needed for the agent):
bookgraph wiki compile /path/to/ws <doc_id> --backend markdown-graph
bookgraph index concepts /path/to/ws              # → wiki/concepts/<slug>.md
```

The agent needs only `segment` + `index build`. `search` and the graph tools also
work before `index build` (live scan of `sections.jsonl`), but concepts
(`get_concept`, `get_context.concepts`) require the index.

## 2. Serve the workspace

```bash
bookgraph mcp /path/to/ws        # stdio transport, bound to this one workspace
```

Tool arguments never take a workspace path — the server is bound to the one you
launched it with.

## 3. Point an MCP client at it

**Claude Code** (`.mcp.json` in the project, or `claude mcp add`):

```json
{
  "mcpServers": {
    "bookgraph": {
      "command": "bookgraph",
      "args": ["mcp", "/path/to/ws"]
    }
  }
}
```

(If `bookgraph` is not on the client's PATH, use the absolute path to the
executable, or `uv run --extra mcp bookgraph mcp /path/to/ws`.)

**Claude Desktop** — the same server entry under `mcpServers` in its config file.

## 4. The reading loop

A self-serve agent drives an entire session with these tools alone:

1. `list_documents()` — discover what there is to read (`doc_id`, `title`,
   `section_count`).
2. `create_plan(doc_id, daily_sections=N)` — start a reading plan (defaults
   `plan_id` to `doc_id`). If a plan with that `plan_id` already exists it
   **errors** rather than wiping its progress — to resume, skip straight to
   `get_next_section`/`list_plans`; pass a fresh `plan_id` for a separate plan,
   or `overwrite=True` to deliberately start it over.
3. Loop:
   - `get_next_section(plan_id)` — the next up-to-`daily_sections` unread
     sections, each with full text + provenance.
   - `get_context(doc_id, section_id)` — the section's content, its graph
     neighbourhood (parent/prev/next/children), and its `concepts`.
   - Pivot as needed:
     - `get_concept(concept_slug)` — where a concept is discussed across **all**
       books (cross-book backlinks).
     - `search(query, doc_id=None)` — find related sections (cross-document when
       `doc_id` is omitted).
     - `get_outline(doc_id)` / `get_related(doc_id, section_id)` — navigate structure.
   - `mark_read(plan_id)` — mark the section read (defaults to the next unread) and
     persist progress.
4. `list_plans()` — resume or report progress across sessions (`completed`/`total`/`done`).

State (reading-plan progress) persists in `reading_plans/<plan_id>.json`, so a new
session resumes exactly where the last left off.

## Agent skills

The repo ships the same **`bookgraph-reader`** workflow in two packaging forms:

- `.claude/skills/bookgraph-reader/SKILL.md` — Claude Code / Claude agents.
- `.agents/skills/bookgraph-reader/SKILL.md` — agent-neutral instructions for
  Hermes, custom MCP clients, or any agent runtime that can load a procedural
  `SKILL.md` file.

Both package this loop — orient → plan → read → explain → follow the concept
graph → track progress. With the MCP server connected, agents should trigger it
on requests like "read the next section" or "walk me through this book", so users
do not have to know the tool names. Copy the matching directory to your client's
skill/procedure location to use it across projects.

## Notes & current limitations

- **Concept quality** is a deterministic tokenizer baseline today (it can surface
  noisy or split concepts). Agent-curated concepts + per-section summaries (the
  "reinforcement" loop) are tracked in issue #21 and not required to read.
- **Ingestion coverage:** documents without useful headings or PDF bookmarks can
  use the token/page fallback segmenter (`bookgraph segment --segmenter token-page`).
- **Write surface:** only `create_plan` and `mark_read` write (reading plans).
  Every other tool is read-only. Client-supplied `doc_id`/`plan_id` are validated
  as filesystem-safe slugs before use.
