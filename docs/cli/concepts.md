# Concept registry & hygiene contract — `concepts/registry.json`

Owner: the `bookgraph concepts` CLI (`bookgraph.cli.concepts` /
`bookgraph.concept_registry`) writes it; the index stage (`bookgraph index build`),
`bookgraph index concepts`, and the MCP `get_concept` / `concept_hygiene` tools read it.

## Why it exists

Tier-1 extraction and Tier-2 agent annotations (see `annotations.md`) both produce
near-duplicate or overly granular concepts as more sections are read. In an Iceberg
corpus, `metadata-file`, `table-metadata`, `latest-metadata-file-pointer`, and
`catalog-pointer` may all be valid, but they need canonicalizing over time, or the
graph fills with noise. The registry is the **human-curated** layer that does this:

- **canonical concepts**: a reviewed slug with an authoritative title;
- **aliases**: deprecated slugs that resolve to one canonical concept (a merge);
- **ignored slugs**: a global prune for generic noun phrases that should never become
  concepts;
- **distinct pairs**: two similar-looking concepts reviewed as *not* duplicates, so
  merge suggestions stop proposing them.

Agents propose (through annotations and the `concept_hygiene` report). Humans decide
(through the CLI). The index applies those decisions on the next build. The MCP
surface stays read-only for the registry.

## Artifact schema

A source of truth like `annotations/`: it is not derived and is never rebuilt. A
missing file means an empty registry.

```json
{
  "canonical": [
    {
      "slug": "table-metadata",
      "title": "Table Metadata",
      "aliases": ["metadata-file", "catalog-pointer"],
      "note": "the root metadata JSON a catalog points at"
    }
  ],
  "ignored": ["however"],
  "distinct": [["snapshot", "snapshot-id"]]
}
```

### Invariants (validated on load and after every CLI mutation)

- Every slug is a filesystem-safe id (`[a-z0-9]+(?:-[a-z0-9]+)*`).
- A canonical slug appears once. An alias belongs to exactly one canonical concept and
  is never itself canonical, so alias chains cannot form.
- An ignored slug is neither canonical nor an alias.
- A present-but-invalid file is an **error** (`Invalid concept registry …`), not an
  empty registry. Silently dropping every alias would un-merge the graph, so
  `index build`, `index concepts`, the `concepts` CLI, and the MCP tools all refuse it.

## Build-time canonicalization (`index build`)

After the Tier-1/Tier-2 merge, each concept edge passes through the registry before
it is stored in `concept_mentions`:

1. An edge whose slug is **ignored** is dropped.
2. An **alias** edge is rewritten to its canonical slug. The original slug is kept in
   the new `concept_mentions.raw_slug` column as provenance (empty when the edge was
   stored under its own slug).
3. A canonical concept's edges take the registry `title`, so every mention aggregates
   under one display title.
4. Two edges in one section that collapse onto the same canonical slug merge into one
   mention. The first edge wins, and the first non-empty gloss is kept.

The registry, like annotations, is **deferred**: editing it touches only the file.
Run `bookgraph index build` (all documents, since aliases are cross-book) to apply it,
then `bookgraph index concepts` to re-render pages.

## Reads

- **`get_concept(concept)`** resolves an alias first: `get_concept("metadata-file")`
  returns the canonical `table-metadata` with `resolved_from: "metadata-file"`,
  `canonical: true`, and `aliases` (the registry aliases plus any `raw_slug` seen on a
  mention). Each mention carries its `raw_slug`. If the alias was registered after
  the last build and the canonical slug is not indexed yet, the alias's own (stale)
  node is served instead of failing.
- **`index concepts`** renders the canonical title and an `Also known as:` line
  listing the aliases. With `--durable-only` it skips concepts that have a lint
  **warning**, so not every noun phrase becomes a durable page. Canonical concepts
  are exempt.

## Hygiene (`bookgraph.concept_hygiene`)

Pure, deterministic, read-only functions over the built index plus the registry:

### Merge suggestions

Pairs of indexed concepts that look like duplicates, best first, each with a score
(0–1), a reason, and a suggested canonical side (a registry-canonical concept, else
the one in more books, then more mentions, then fewer words):

| Reason | Score | Example |
| --- | --- | --- |
| inflection or word-order variant | 1.0 | `snapshots` / `snapshot` |
| acronym | 0.9 | `mor` / `merge-on-read` |
| one concept's words subsume the other's | shorter ÷ longer word count (×0.8 for a single word) | `metadata-file` / `latest-metadata-file-pointer` = 0.5 |
| near-identical spelling | `difflib` ratio ≥ 0.88 | `partiton-evolution` / `partition-evolution` |

The default threshold is 0.5. A lone head noun inside a phrase (`table` /
`table-metadata` = 0.4) falls below it. Ignored slugs, aliases (already merged), and
distinct pairs are never suggested.

### Lint

| Rule | Severity | Meaning |
| --- | --- | --- |
| `generic-term` | warning | every word is generic (`however`, `example`, `chapter`, …) |
| `one-off` | warning | auto-extracted and mentioned in exactly one section |
| `over-granular` | info | a phrase of four or more words; consider aliasing it to a broader concept |
| `stale-alias` | warning | the slug is now an alias, but the index has not been rebuilt |
| `stale-ignored` | warning | the slug is now ignored, but the index has not been rebuilt |

Registry-canonical concepts are exempt from the noise rules. A concept is **durable**
(worth a `wiki/concepts/` page under `--durable-only`) when it has no `warning`.

### Review queue

Concepts with at least one `source='agent'` mention that are not yet canonical,
aliased, or ignored. Each item carries its totals, the first agent gloss, and the
agent-annotated sections. A reviewer clears an item by accepting it (`canonical`),
merging it (`alias`), or pruning it (`ignore`).

## CLI — `bookgraph concepts`

Report commands need a built index. Write commands edit only
`concepts/registry.json`, then print `registry: <path>` and a reminder to run
`bookgraph index build`.

```bash
bookgraph concepts suggest  WS [--threshold 0.5] [--limit 50]  # "0.50  alias -> canonical  (reason)"
bookgraph concepts lint     WS [--limit 200]                   # "warning  slug  [rule] message"
bookgraph concepts review   WS [--limit 50]                    # pending agent concepts
bookgraph concepts alias    WS ALIAS CANONICAL [--title T]     # merge ALIAS into CANONICAL
bookgraph concepts unalias  WS ALIAS                           # detach an alias
bookgraph concepts canonical WS SLUG [--title T] [--note N]    # accept / retitle
bookgraph concepts ignore   WS SLUG                            # global prune
bookgraph concepts distinct WS LEFT RIGHT                      # silence a suggestion
```

- `alias`: creates `CANONICAL` in the registry if needed. Its title is `--title`,
  else the indexed title, else one derived from the slug. Aliasing a slug that was
  itself canonical moves its aliases to the new canonical. Aliasing *to* an alias is
  rejected (`'x' is an alias of 'y'; alias … to 'y' instead`).
- `canonical`: promotes a slug that was an alias or ignored, removing it from that
  role.
- `ignore`: refuses a canonical slug.

## MCP — `concept_hygiene(limit=20, threshold=0.5)`

Read-only. Returns `concept_count`, `merge_suggestions`, `lint`, and `review_queue`,
each capped at `limit`. A reading agent can surface the report to its user, but
registry decisions go through the CLI.

## Must not do

- The index must never write the registry, and the registry CLI must never write the
  index. Each stays single-writer.
- Canonicalization must not discard provenance: an aliased mention keeps its
  `raw_slug`, and annotation files keep the agent's original slugs.
