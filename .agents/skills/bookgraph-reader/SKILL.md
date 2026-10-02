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

For raw registered PDFs, replace `parse` with `add-book` + `parse-book`. For
large PDFs, `parse-book` can take a long time and should expose a durable log path
under `runs/parse-book/`; do not silently wait in an agent/cron job without that
log. See `docs/cli/parse-book-large-pdfs.md`.

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
- `complete_reading_batch(plan_id, ...)` — advance a whole batch only once its
  enrichment (annotation, translation, figures, index) is verified; use instead of
  `mark_read` for annotate/translate jobs. `validate_reading_batch` is the dry run.

Optional navigation tools:

- `search(query, doc_id=None)` — find sections by topic; omit `doc_id` for
  cross-document search.
- `get_outline(doc_id, root_id=None, max_depth=None)` — show document hierarchy; scope it (`max_depth=1`, then `root_id`) on large books.
- `get_section_tree(doc_id, section_id)` — show a section's breadcrumb, siblings, and children.
- `get_chapter_outline(plan_id, chapter_level=None)` — show the current chapter's outline with read flags and in-chapter progress; use `chapter_level=2` when chapters sit under parts (a lone book-title root is skipped automatically). While a Part or book-root heading is itself next, it comes back alone (`total == 1`) — expected; mark it read and move on, don't retry.
- `get_related(doc_id, section_id)` — show parent/prev/next/children neighbours.
- `get_concept(concept)` — show cross-book mentions for a concept.
- `get_plan_progress(plan_id, chapter_level=None)` — current chapter, sections
  left in it, and the next chapter boundary, without section text. A lone
  `# Book Title` root is skipped automatically; pass `chapter_level=2` when
  chapters sit under level-1 parts.

Optional translation cache tools (when the user wants sections translated):

- `get_section_translation(doc_id, section_id, lang)` — reuse `content` only when
  `status` is `fresh` and (`includes_assets` or not `section_has_assets`);
  `stale`/`missing`, or a fresh prose-only translation of a section with
  figures/tables, means translate again.
- `write_section_translation(doc_id, section_id, lang, content, includes_assets=...,
  source_section_hash=<current_section_hash>)` — cache a new translation.
- `list_section_artifacts(doc_id=None, lang=None)` — list cached translations and
  their freshness.

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
   - Use `get_chapter_outline` / `get_section_tree` to orient; use a scoped `get_outline` to jump.
   - Use `get_related` to move around the current section.
   - Use `get_concept` when a concept should be connected across books.

5. **Advance only after the user is done**
   - Do not mark a section read before presenting it.
   - When the user says to continue, or confirms they are done, call
     `mark_read(plan_id)` and repeat from step 3.
   - When the user asks how much of the chapter is left, call
     `get_plan_progress(plan_id)` (or read `chapter` from `get_next_section`)
     instead of fetching the outline.
   - When pausing, call `list_plans()` and report `completed/total`.
   - **Enrichment jobs** (annotate/translate/index per batch): do not call
     `mark_read`. Call `complete_reading_batch(plan_id, inspected_assets=[...],
     translation_lang=..., ...)` after the work; if `committed` is false, fix every
     blocking `issue` it lists (each says what to do) and call it again. Never
     advance progress past a failed step.

## Behavior rules

- Stay grounded in section text and provenance.
- Do not dump an entire book; proceed by reading-plan ticks.
- Ask before overwriting an existing plan. `create_plan` should not be used to
  reset progress unless the user explicitly wants that.
- Treat auto-extracted concepts as hints, not authoritative labels.
- Prefer deterministic BookGraph tools over web/general knowledge for book
  content questions.
- Only `create_plan`, `mark_read`, and `complete_reading_batch` write reading
  progress; `annotate_section` and `write_section_translation` write their
  artifacts. Treat all other tools as read-only.

## Client-specific packaging

- Claude agents can use the Claude-tuned equivalent at
  `.claude/skills/bookgraph-reader/SKILL.md`; keep the tool loop and argument
  names in sync when either skill changes.
- Other agents can copy this directory to their own skill/procedure location or
  load this file as repository instructions.
- Full setup details live in `docs/mcp/reading-agent.md`.
