# Annotations contract — `annotations/<doc_id>/<section_id>.json`

Owner: the MCP `annotate_section` tool (`bookgraph.mcp.service`) writes them; the
index stage (`bookgraph index build` / `bookgraph.annotations`) reads them.

## Why they exist — the two-tier concept graph

Concepts have two tiers of provenance:

- **Tier 1 (auto):** the deterministic extractor `bookgraph.concepts.extract_concepts`
  (shared by `index build` and the `markdown-graph` wiki backend) is the cold-start
  baseline. It guesses concepts from title-case / long-token heuristics, so the graph
  is never empty before any agent has read a document — but it produces false positives
  and misses real concepts.
- **Tier 2 (agent):** a reading agent that already reads each section to explain it can
  identify the section's *real* concepts and write a per-concept gloss + a per-section
  summary. `annotate_section` captures that judgment as a **source artifact** so it
  **enriches the concept graph over time** — a reinforcement / compounding loop rather
  than leaving the deterministic baseline untouched.

Each annotation is the **authoritative concept→section edge set for that one section**:
it prunes the tokenizer's spurious mentions *and* adds real ones. **The agent wins per
annotated section.**

## Deferred, not live

`annotate_section` writes **only** the source artifact below. It does **not** touch
`indexes/bookgraph.db`. The concept graph re-merges Tier-1 + Tier-2 on the next
`bookgraph index build <doc_id>`. This keeps the index a purely derived / rebuildable
artifact and keeps it single-writer (only `index build` writes the database).

Consequence: after `annotate_section`, a section's `summary` is visible immediately via
MCP `get_context` (which reads the annotation file directly), but the annotation's
concept edges (and their gloss/source) appear in `get_concept` / `get_context.concepts`
/ `wiki/concepts` only after the next `index build` for that document.

## Artifact schema

One file per annotated section, ranking alongside
`sources/sections/<doc_id>/sections.jsonl` as a source of truth (it is **not** derived
and is **not** rebuilt by `index build` — the index reads it, never writes it):

```text
annotations/<doc_id>/<section_id>.json
```

```json
{
  "doc_id": "ddia",
  "section_id": "ddia.schema-evolution",
  "concepts": [
    {"slug": "schema-evolution", "title": "Schema Evolution", "gloss": "why it matters here"}
  ],
  "summary": "the agent's explanation of this section",
  "model": "claude-...",
  "created_at": "2026-08-13T00:00:00Z"
}
```

### Field rules

- `doc_id`: the annotated section's document; a filesystem-safe slug
  (`[a-z0-9]+(?:-[a-z0-9]+)*`). Doubles as the `annotations/<doc_id>/` folder name, so
  it is validated on write.
- `section_id`: the annotated section; has the `<doc_id>.<slug>` form (it contains a
  dot, so it is **not** a bare slug). Doubles as the `<section_id>.json` filename.
  Validated by **membership** — it must be a section that exists in the document — not
  by the slug regex.
- `concepts`: the section's authoritative concept edges (possibly empty — see the merge
  rule). Each is a `{slug, title, gloss}`:
  - `slug`: derived by slugifying `title`; an empty or `untitled` slug is **rejected**
    (the whole annotation write fails) rather than silently written.
  - `title`: display title for the concept.
  - `gloss`: a short per-section note on why the concept matters *here*. May be empty.
  - Concepts are **deduplicated by slug**: the first occurrence of a slug wins its
    title, and the first non-empty gloss for that slug wins.
- `summary`: the agent's explanation of the section. May be empty. Surfaced immediately
  by `get_context`.
- `model`: optional identifier of the model that produced the annotation.
- `created_at`: optional ISO-8601 timestamp.

## Merge rule (presence-based)

This is the sharpest correctness point. When `index build` (re)builds a document, each
section's concept edges come from **exactly one** tier, chosen by the **presence of an
annotation file for that section** — never by whether its `concepts` list is truthy:

```text
annotations = read_annotations_for_doc(workspace, doc_id)   # dict keyed by section_id
for each section:
    if section.id in annotations:            # agent wins — EVEN IF concepts == []
        edges = agent_edges(annotations[section.id])   # source="agent", carries gloss
    else:
        edges = auto_edges(extract_concepts(sections), section.id)   # source="auto", gloss=""
```

- Branch on **presence**, never on `concepts` truthiness. An annotation with
  `concepts: []` is the tokenizer-false-positive fix: it must **zero out** that section's
  concept rows, not fall through to the auto extractor.
- A given `(doc_id, section_id)` therefore only ever holds rows from **one** source. The
  `concept_mentions` primary key `(doc_id, section_id, concept_slug)` guarantees there is
  never a Tier-1/Tier-2 collision for the same pair.
- Auto edges carry `source="auto"` and an empty `gloss`; agent edges carry
  `source="agent"` and the annotation's gloss. See `index.md` for the column contract.

## Non-goal: the `markdown-graph` wiki backend

The enrichment surfaces are **concept pages** (`wiki/concepts/*.md`, gloss +
`(agent-verified)` marker) and **MCP `get_context`** (immediate `summary`).

Rendering summaries or agent-curated concepts on the `markdown-graph` backend's per-book
section pages (`wiki/books/<doc_id>/sections/`) is an **explicit non-goal** here: that
backend is stateless and book-local, extracts its own deterministic wikilinks, and would
need a `WikiBackend` signature change to receive annotations. The default wiki backend is
`llmwiki` (which renders zero concept links) anyway; the cross-book concept graph this
contract enriches is produced solely by `bookgraph index concepts` reading
`bookgraph.db`.

## Mutation rules

- `annotate_section` writes **only** under `annotations/<doc_id>/`. It must not touch
  `indexes/`, `sources/`, `wiki/`, or `reading_plans/`.
- Re-annotating a section overwrites its single file (last write wins); annotations are
  per-section, so annotating one section never affects another.
- The index reads annotations but never writes or deletes them — deleting an annotation
  file and re-running `index build` cleanly reverts that section to the Tier-1 baseline.
