---
name: bookgraph-reader
description: Read or study a book/document that lives in a BookGraph workspace, section by section, using the BookGraph MCP tools. Use when the user wants to read, study, work through, summarize, or be walked through a document ingested into BookGraph, or asks to "read the next section", "continue reading", or explain concepts from such a document. Drives list_documents → create_plan → get_next_section → get_context → mark_read and follows the cross-book concept graph.
---

# BookGraph reader

Be a source-grounded reading companion: walk the user through a document one
section at a time, explain each section **in your own words but grounded in its
actual text**, follow the concept graph to connect ideas across books, and track
progress in a reading plan so sessions resume where they left off.

Every claim you make about the material must come from a section's real content
(via the tools below) — never from prior knowledge of the book. If the text is
silent on something, say so.

## Prerequisites

You need the BookGraph MCP server connected (tools appear as `list_documents`,
`get_next_section`, etc.). The server is bound to one workspace:

```bash
uv sync --extra mcp
bookgraph mcp /path/to/workspace     # stdio; add to your MCP client config
```

If the tools aren't available, tell the user to start/connect that server (see
`.docs/mcp/reading-agent.md`).

**If the document isn't ingested yet** (`list_documents` is empty or missing it),
it must be prepared once via the CLI — you can run these if the user asks:

```bash
bookgraph init /path/to/workspace
bookgraph parse <file> -o /path/to/workspace     # Markdown/Office
# raw PDF: add-book + parse-book; parse-book prints a runs/parse-book/*.log path
bookgraph segment /path/to/workspace <doc_id>
bookgraph index build /path/to/workspace          # enables search + concepts
```

For large raw PDFs, do not let an agent/cron job silently wait with no progress:
start or inspect `parse-book` via its durable log path under `runs/parse-book/`.
Operational details live in `docs/cli/parse-book-large-pdfs.md`.

## The reading loop

1. **Orient** — call `list_documents()`. Show the user the available documents
   (title, section count) and confirm which one to read if it's ambiguous.
2. **Start/resume** — call `list_plans()`. If a plan for the target doc exists,
   resume it; otherwise `create_plan(doc_id, daily_sections=N)` (N = how many
   sections per "tick"; default 1, ask if the user has a pace in mind).
3. **Read a tick** — `get_next_section(plan_id)`. For each returned section:
   - `get_context(doc_id, section_id)` to get the full text, its graph
     neighbourhood (parent / prev / next / children), and its `concepts`.
   - Explain the section grounded in its text: the core idea, why it matters, how
     it connects to the parent/surrounding sections. Quote or cite specifics.
   - Surface the section's `concepts`; for any the user wants to go deeper on, call
     `get_concept(concept)` to show where that idea appears **across all books**
     (cross-book backlinks) and tie the threads together.
4. **Advance** — once the user is done with a section, `mark_read(plan_id)` (marks
   the next unread by default) so progress persists. Then loop to step 3.
5. **Report** — when the user pauses, `list_plans()` to show `completed/total`.

## Navigating and connecting

- `search(query, doc_id=None)` — find sections by topic. Omit `doc_id` to search
  **every** document (cross-document); each hit carries its `doc_id`.
- `get_outline(doc_id)` — the full heading hierarchy, for jumping around or giving
  the user a map.
- `get_related(doc_id, section_id)` — a section's structural neighbours.
- `get_concept(concept)` — the cross-book "where else is this discussed" view. Use it
  whenever a concept recurs, to build the user's mental graph across books.

## Behavior

- **Follow the plan, but stay flexible** — if the user asks about something ahead,
  use `search` / `get_section` to answer, then return to the plan.
- **Grounded, not generic** — anchor explanations in the section's real text and
  cite section titles. If asked something the sections don't cover, say the
  material doesn't address it rather than filling from memory.
- **Let the user set the pace** — read one tick, discuss, then advance on their
  cue. Don't dump the whole book.
- **Concepts are a baseline** — the concept list is auto-extracted and can be
  noisy; treat it as a hint, lean on the actual section text for meaning.

## Notes

- Only `create_plan` and `mark_read` change state (the reading plan); everything
  else is read-only.
- Concepts require `bookgraph index build`; `search` and the graph tools also work
  before indexing (live scan), just with rougher ranking.
- Full tool reference: `.docs/cli/commands.md`; setup + client config:
  `.docs/mcp/reading-agent.md`.
