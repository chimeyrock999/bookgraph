---
name: bookgraph-reader
description: Agent-neutral reading workflow for BookGraph workspaces. Use when any AI agent/MCP client should read, study, summarize, explain, or continue through a document ingested into BookGraph. The workflow is list_documents → list_plans/create_plan → get_next_section → get_context/search/get_concept → mark_read.
---

# BookGraph reader skill

Use this skill when a user asks an agent to read, study, summarize, explain, or
continue through a document stored in a BookGraph workspace. It is intentionally
client-neutral: Claude, Hermes, custom MCP clients, and other agents can all use
this same loop once the BookGraph MCP server is connected.

The agent is a **source-grounded reading companion**. Every claim about a book or
document must come from the BookGraph section artifacts returned by MCP tools. If
the retrieved text does not support an answer, say that the material does not
address it rather than filling from memory.

## Prerequisites

A workspace must already contain section artifacts. Indexing is recommended for
search, graph context, and cross-book concepts.

```bash
uv sync --extra mcp
bookgraph init /path/to/workspace
bookgraph parse /path/to/book.md -o /path/to/workspace
bookgraph segment /path/to/workspace <doc_id>
bookgraph index build /path/to/workspace
bookgraph mcp /path/to/workspace
```

For raw registered PDFs, replace `parse` with `add-book` + `parse-book`.

The MCP server is bound to one workspace. Tool calls should not ask for arbitrary
workspace paths; they operate on the workspace used when `bookgraph mcp` started.

## Required MCP tools

- `list_documents()` — discover available documents and section counts.
- `list_plans()` — discover existing reading plans and progress.
- `create_plan(doc_id, daily_sections=N, plan_id=None)` — start a resumable plan.
- `get_next_section(plan_id)` — fetch the next unread section(s), including text.
- `get_context(doc_id, section_id)` — fetch section text, structural neighbours,
  and concepts.
- `mark_read(plan_id, section_id=None)` — advance reading progress.

Optional navigation tools:

- `search(query, doc_id=None)` — find sections by topic; omit `doc_id` for
  cross-document search.
- `get_outline(doc_id)` — show document hierarchy.
- `get_related(doc_id, section_id)` — show parent/prev/next/children neighbours.
- `get_concept(slug)` — show cross-book mentions for a concept.

## Reading loop

1. **Orient**
   - Call `list_documents()`.
   - If there are multiple candidates, ask the user which `doc_id` to read.
   - If no document exists, explain that ingestion/segmentation must run first.

2. **Start or resume**
   - Call `list_plans()`.
   - If a plan exists for the chosen document, resume it.
   - Otherwise call `create_plan(doc_id, daily_sections=N)`. Use `N=1` unless the
     user asked for a faster pace.

3. **Read a tick**
   - Call `get_next_section(plan_id)`.
   - For each returned section, call `get_context(doc_id, section_id)`.
   - Explain the section in plain language, grounded in the returned text.
   - Cite section title/ID and quote short relevant snippets when useful.
   - Mention structural context: parent, previous/next, children, and concepts.

4. **Follow connections on demand**
   - Use `search` when the user asks where a topic appears.
   - Use `get_outline` to orient or jump.
   - Use `get_related` to move around the current section.
   - Use `get_concept` when a concept should be connected across books.

5. **Advance only after the user is done**
   - Do not mark a section read before presenting it.
   - When the user says to continue, or confirms they are done, call
     `mark_read(plan_id)` and repeat from step 3.
   - When pausing, call `list_plans()` and report `completed/total`.

## Behavior rules

- Stay grounded in section text and provenance.
- Do not dump an entire book; proceed by reading-plan ticks.
- Ask before overwriting an existing plan. `create_plan` should not be used to
  reset progress unless the user explicitly wants that.
- Treat auto-extracted concepts as hints, not authoritative labels.
- Prefer deterministic BookGraph tools over web/general knowledge for book
  content questions.
- Only `create_plan` and `mark_read` write state. Treat all other tools as
  read-only.

## Client-specific packaging

- Claude agents can use the mirrored skill at
  `.claude/skills/bookgraph-reader/SKILL.md`.
- Other agents can copy this directory to their own skill/procedure location or
  load this file as repository instructions.
- Full setup details live in `.docs/mcp/reading-agent.md`.
