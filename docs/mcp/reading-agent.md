# Integrating a reading agent over MCP

This guide wires an AI agent (any MCP client — Claude Code, Claude Desktop, or a
custom client) to a BookGraph workspace so it can read a document section by
section, follow the concept graph, and track progress — with **no CLI step**
between "workspace prepared" and "agent reading".

The MCP server (`bookgraph mcp`) serves one workspace over stdio and exposes only
the tools below; it never reads the wiki files — it serves from
`sources/sections/` + `indexes/bookgraph.db` (see `docs/cli/index.md`).

## 1. Prepare a workspace (one-time, per corpus)

```bash
uv sync --extra mcp

bookgraph init /path/to/ws
# ingest each document (Markdown/Office via `parse`; raw PDF via add-book + parse-book)
# parse-book prints a runs/parse-book/*.log path for long raw-PDF runs
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
     neighbourhood (parent/prev/next/children), its `concepts`, and any `summary`
     already written for it.
   - Pivot as needed:
     - `get_concept(concept)` — where a concept is discussed across **all**
       books (cross-book backlinks). Defaults to a compact card (bare backlinks
       with per-section glosses) for lightweight traversal; pass
       `include_annotations=True` for the detail view, where each mention also
       carries its section's Tier-2 `summary`, so a concept with several mentions
       reads as a source-grounded note.
     - `search(query, doc_id=None)` — find related sections (cross-document when
       `doc_id` is omitted).
     - `get_outline(doc_id)` / `get_related(doc_id, section_id)` — navigate structure.
   - `annotate_section(doc_id, section_id, concepts=[...], summary="...")` — **feed
     your judgment back** (see *The reinforcement loop* below): the real concepts of
     the section (each `{title, gloss}`, `slug` optional) and a prose summary. This is
     optional per section but is how the concept graph gets smarter over time.
   - `mark_read(plan_id)` — mark the section read (defaults to the next unread) and
     persist progress.
4. `list_plans()` — resume or report progress across sessions (`completed`/`total`/`done`).

State (reading-plan progress in `reading_plans/<plan_id>.json` and annotations in
`annotations/<doc_id>/<section_id>.json`) persists on disk, so a new session resumes
exactly where the last left off and keeps every annotation ever written.

## The reinforcement loop

The concept graph has two tiers (see `docs/cli/annotations.md`):

- **Tier 1 (auto):** a deterministic tokenizer extracts concepts at `index build` time,
  so the graph is never empty — but it is "dumb" (false positives, missed concepts).
- **Tier 2 (agent):** `annotate_section` records the *real* concepts you found while
  reading, each with a gloss, plus a section summary. Your annotation is the
  **authoritative concept→section edge set for that section**: on the next rebuild it
  prunes the tokenizer's spurious mentions and adds the real ones (an empty `concepts`
  list deliberately zeroes out that section's concepts).

`annotate_section` is **deferred**: it writes only the annotation file — it does not
touch the index. Two things follow:

- The `summary` shows up **immediately** via `get_context` (it reads the annotation
  file directly).
- The concept edges (and their gloss / `(agent-verified)` marker on `wiki/concepts`)
  take effect only after the next per-document rebuild.

So annotations **compound**: read a book once with light auto concepts, and each night's
rebuild folds in whatever the agent annotated, so the cross-book graph gets sharper the
more it is read. A nightly (or post-session) maintenance step re-merges and re-renders:

```bash
bookgraph index build /path/to/ws              # per-doc: re-merge Tier-1 + Tier-2
bookgraph index concepts /path/to/ws           # global: re-render wiki/concepts/<slug>.md
```

## Agent skills

The repo ships the **`bookgraph-reader`** workflow in two packaging forms:

- `.claude/skills/bookgraph-reader/SKILL.md` — Claude Code / Claude agents
  (Claude-tuned equivalent).
- `.agents/skills/bookgraph-reader/SKILL.md` — agent-neutral instructions for
  Hermes, custom MCP clients, or any agent runtime that can load a procedural
  `SKILL.md` file.

Both package the same loop — orient → plan → read → explain → follow the concept
graph → track progress — but the prose can be tuned for the client. Keep the tool
loop and argument names in sync when either skill changes. With the MCP server
connected, agents should trigger it on requests like "read the next section" or
"walk me through this book", so users do not have to know the tool names. Copy the
matching directory to your client's skill/procedure location to use it across
projects.

## Notes & current limitations

- **Concept quality** starts from a deterministic tokenizer baseline (it can surface
  noisy or split concepts) and improves as the agent annotates: `annotate_section`
  curates concepts + per-section summaries (the reinforcement loop above). Annotating
  is optional to read but is what sharpens the graph over time.
- **Ingestion coverage:** documents without useful headings or PDF bookmarks can
  use the token/page fallback segmenter (`bookgraph segment --segmenter token-page`).
- **Write surface:** only `create_plan` and `mark_read` write reading plans, and
  `annotate_section` writes a per-section annotation artifact. Every other tool is
  read-only. Client-supplied `doc_id`/`plan_id` are validated as filesystem-safe slugs
  before use; `annotate_section`'s `section_id` (which contains a dot, so it is not a
  bare slug) is validated by membership against the document's sections.
