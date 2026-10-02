# CLI Artifact Contracts

This file defines filesystem artifact schemas shared by CLI stages.

## `sources/inbox/<book_id>/book.json`

Owner: `bookgraph add-book` and future book-level orchestration commands.

Current schema:

```json
{
  "book_id": "designing-data-intensive-applications",
  "title": "Designing Data Intensive Applications",
  "source_type": "pdf",
  "source_path": "/absolute/original/input/path/book.pdf",
  "workspace_path": "/absolute/workspace/path",
  "status": "registered",
  "pdf": {
    "title": "Designing Data-Intensive Applications",
    "author": "Martin Kleppmann",
    "pages": 616,
    "has_bookmarks": true,
    "bookmarks": [
      {"title": "Chapter 1. Reliable, Scalable, and Maintainable Applications", "page_index": 1, "level": 1}
    ]
  },
  "pipeline": {
    "parser": null,
    "segmenter": null,
    "wiki_backend": null
  },
  "paths": {
    "book_root": "/absolute/workspace/sources/inbox/<book_id>",
    "original": "/absolute/workspace/sources/inbox/<book_id>/original.pdf",
    "parsed": "/absolute/workspace/sources/parsed/<book_id>",
    "sections": "/absolute/workspace/sources/sections/<book_id>",
    "wiki": "/absolute/workspace/wiki/books/<book_id>"
  }
}
```

### Field rules

- `book_id`: stable slug derived from title/path unless explicitly overridden by a future option.
- `title`: human title derived from filename unless explicitly overridden by a future option.
- `source_type`: currently `pdf` only for `add-book`.
- `source_path`: absolute path to the user-provided source path at registration time.
- `workspace_path`: absolute workspace path used at registration time.
- `status`: current book-level lifecycle state.
- `pdf`: best-effort PDF metadata read at registration time. If optional `pypdf`
  support is unavailable or the source cannot be inspected, values fall back to
  `title: null`, `author: null`, `pages: 0`, `has_bookmarks: false`, and an
  empty `bookmarks` list. Bookmark `page_index` is zero-based when known and
  `level` preserves nested outline depth.
- `pipeline`: per-stage selected plugin/status placeholder. Current registration sets all values to `null`.
- `paths`: absolute target paths for downstream stages.

### Status values

Current implemented value:

- `registered`: raw source copied/registered; no parser/segmenter/wiki stage has run.

Future values must be added here before implementation, likely:

- `parsed`
- `segmented`
- `wiki_built`
- `failed`

## `sources/inbox/<book_id>/original.pdf`

Owner: `bookgraph add-book`.

Rules:

- Byte copy of the registered PDF source.
- Must keep `.pdf` extension.
- Must not be modified by parser/segmenter/wiki commands.

## `sources/parsed/<doc_id>/document.json`

Owner: `bookgraph parse` and future parser commands.

Current schema mirrors `bookgraph.models.Document`:

```json
{
  "doc_id": "deep-work",
  "title": "Deep Work",
  "blocks": [
    {
      "id": "b0",
      "type": "title",
      "text": "Deep Work",
      "level": 1,
      "page_idx": null,
      "bbox": null,
      "source_path": "/absolute/source/path.md",
      "order": 0,
      "metadata": {
        "line_start": 1,
        "line_end": 1
      }
    }
  ],
  "metadata": {
    "parser": "markdown",
    "source_path": "/absolute/source/path.md"
  }
}
```

### Document field rules

- `doc_id`: stable slug used as output folder name.
- `title`: parser-derived title if available; fallback to source filename/title.
- `blocks`: ordered canonical content blocks.
- `metadata.parser`: parser plugin name that produced the document.
- `metadata.source_path`: absolute source path parsed by the command.

### Block field rules

- `id`: stable within the document. Current adapters use order/page-based ids.
- `type`: one of canonical block types from `models.py`: `title`, `text`, `list`, `table`, `image`, `chart`, `equation`, `unknown`.
- `text`: normalized text content for downstream segmenters.
- `level`: heading/title level if known.
- `page_idx`: page index if known from paged parser output.
- `bbox`: source bounding box if known from layout parser output.
- `asset_path`: for `image`/`table`/`chart` blocks, the parser-relative filename of the
  extracted asset (e.g. MinerU's `fig1.jpg`, staged under `sources/parsed/<doc_id>/images/`).
  `null` for text blocks. Surfaced per section by the MCP `get_section` / `get_context`
  `assets` list (path, type, caption, order) so readers need not grep this file.
- `source_path`: path to source/parser artifact that proves the block.
- `order`: zero-based reading order.
- `metadata`: parser-specific provenance. Must be JSON scalar values only.

### Provenance rules for converting adapters

When an adapter converts the original source into Markdown before building
blocks, block-level and document-level provenance point at different files:

- block `source_path` is the **staged artifact the block was read from**, because
  `metadata.line_start` / `line_end` are line numbers in that artifact. Line 3 of
  a `.docx` has no meaning; line 3 of the staged Markdown does.
- `document.metadata.source_path` stays the **original user-provided source**.
- `document.metadata.markdown_path` records the staged Markdown artifact.

Current behavior:

| Parser | block `source_path` | `metadata.source_path` |
| --- | --- | --- |
| `markdown` | the `.md` source itself | same `.md` source |
| `markitdown` | `sources/parsed/<doc_id>/<doc_id>.md` | original `.docx`/`.pptx`/… |
| `mineru-middle-json` | the `*_middle.json` file | same `*_middle.json` file |

### List block rules

`list` blocks keep reading structure rather than flattening to bullets:

- ordered lists keep their numbers, including a non-default `start`;
- nested lists keep hierarchy as two-space indentation per depth level.

## Parser side artifacts

Parser commands may write side artifacts only under `sources/parsed/<doc_id>/`.

Allowed examples:

```text
sources/parsed/<doc_id>/<doc_id>.md                  # staged markdown from MarkItDown or MinerU
sources/parsed/<doc_id>/assets/...                   # extracted images/assets
sources/parsed/<doc_id>/<doc_id>_middle.json         # staged MinerU middle JSON
sources/parsed/<doc_id>/<doc_id>_layout.pdf          # MinerU layout debug PDF
sources/parsed/<doc_id>/<doc_id>_span.pdf            # MinerU span debug PDF
sources/parsed/<doc_id>/<doc_id>_content_list.json   # MinerU content-list JSON
sources/parsed/<doc_id>/images/...                   # MinerU extracted images
```

MinerU runner staging contract, once wired by a future backend command:

- `MinerURunner.run(original_pdf, sources/parsed/<doc_id>)` invokes the MinerU CLI.
- It stages artifacts flat under `sources/parsed/<doc_id>/` using `<doc_id>` as the filename stem.
- It does not produce `document.json`; `mineru-middle-json` remains the parser that turns `<doc_id>_middle.json` into canonical blocks.

If a parser writes side artifacts, it should reference them from `document.metadata` when useful.

## Placeholder request artifacts

Owner: CLI interface commands that reserve a future backend operation without
running it.

Path:

```text
runs/cli-placeholders/<command>-<id>.json
```

Common schema:

```json
{
  "command": "parse-book",
  "status": "placeholder",
  "book_id": "deep-work",
  "runner": {
    "name": "mineru",
    "command": "mineru",
    "method": "auto",
    "backend": null,
    "timeout_seconds": 3600
  },
  "parser": "mineru-middle-json",
  "inputs": {},
  "intermediate_outputs": {},
  "outputs": {},
  "backend_not_run": true
}
```

Rules:

- Placeholder artifacts are coordination contracts, not completed stage outputs.
- They must not be written into `sources/parsed`, `sources/sections`, `wiki`, or
  `reading_plans`.
- `backend_not_run` must be `true`.
- Backend agents can use these files to see the agreed command inputs/outputs.

## `sources/sections/<doc_id>/sections.jsonl`

Owner: the segment stage (`bookgraph segment` command / `bookgraph.sections.write_sections`).

Canonical machine-readable section manifest. One JSON object per line, each
mirroring `bookgraph.models.Section`:

```json
{"id": "ddia.chapter-3-storage", "doc_id": "ddia", "title": "Chapter 3. Storage", "level": 1, "heading_path": ["Chapter 3. Storage"], "page_start": 10, "page_end": 11, "text": "Opening paragraph.", "prev_id": null, "next_id": "ddia.sstables-and-lsm-trees", "block_ids": ["b1", "b2"], "metadata": {}}
```

### Section field rules

- `id`: `<doc_id>.<slug>` derived from the section title. Doubles as the
  `<section_id>.md` filename, so it must be unique within a document; the writer
  refuses duplicate ids rather than overwriting.
- `doc_id`: parent document id; matches the `sources/parsed/<doc_id>/` folder.
- `heading_path`: heading ancestry from the document root to this section.
- `page_start` / `page_end`: page span if known from paged parser output.
- `prev_id` / `next_id`: linear reading-order neighbours, `null` at the ends.
- `block_ids`: provenance back to `document.json` block ids that prove the section.

## `sources/sections/<doc_id>/<section_id>.md`

Owner: the segment stage (`bookgraph.sections.write_sections`).

Human-readable reading unit: YAML frontmatter carrying the same provenance
fields (`id`, `doc_id`, `title`, `level`, `heading_path`, `page_start`,
`page_end`, `prev_id`, `next_id`, `block_ids`) followed by the section heading
and text. Frontmatter values are emitted as JSON scalars/arrays (valid YAML) so
titles with colons or quotes cannot corrupt the frontmatter.

> Note: `sources/sections/` is owned by the segment stage. Wiki backends should
> read this manifest and emit compiled output under `wiki/`, not rewrite section
> source artifacts.

## `sources/sections/<doc_id>/quality.json`

Owner: the segment stage (`bookgraph segment` command / `bookgraph.quality`).

Ingest data-quality report for one document: every anomaly the deterministic
checks in `bookgraph.quality` found in the freshly written sections, so a bad
parse is visible at ingest time instead of during reading.

```json
{
  "doc_id": "iceberg",
  "section_count": 109,
  "warning_count": 2,
  "warning_counts": {"asset_captions_only": 1, "asset_type_ambiguous": 1},
  "warnings": [
    {
      "section_id": "iceberg.snapshots",
      "code": "asset_type_ambiguous",
      "message": "asset p12.b3: caption reads as a 'image' but the parser classified the block as 'table'; treat it as 'image' or open the file to confirm",
      "block_id": "p12.b3"
    }
  ]
}
```

### Warning codes

| Code | Meaning |
| --- | --- |
| `page_range_inverted` | `page_start > page_end` — the section's page provenance is unreliable. |
| `page_range_incomplete` | Exactly one of `page_start` / `page_end` survived parsing. |
| `asset_type_ambiguous` | The asset's caption label contradicts the parser's block type (a figure emitted as a `table`, say). Carries `block_id`. |
| `asset_captions_only` | The section's `text` is effectively just its asset captions; the labels/tabular data live inside the asset files. |
| `asset_text_sparse` | A figure/table-heavy section (2+ assets) with almost no prose beyond the captions. |
| `asset_file_missing` | The section references an asset file that is not available under `sources/parsed/<doc_id>/` (never staged, remote, or outside the workspace). Carries `block_id`. |

Codes are stable identifiers; `message` is display text and may be reworded.
A document with no anomalies still gets a report, with `warning_count: 0` and an
empty `warnings` array. The report carries no timestamps, so re-segmenting
unchanged input rewrites it byte-identically.

The same checks back the MCP section APIs, which attach the per-section warnings
to every section they return (`SectionView.warnings`, see `commands.md`), so a
reading agent never has to open `sources/parsed/<doc_id>/document.json` to learn
that a section's provenance is broken. Both sides also decide whether an asset
exists through the one resolver in `bookgraph.assets`, so a reference the reader
cannot open is reported as `asset_file_missing` by ingest and by `get_section`
alike — never counted as an asset on one side and dropped on the other.

## `wiki/books/<doc_id>/`

Owner: the wiki stage (`bookgraph wiki compile` command / wiki backend plugins).

The `llmwiki` backend writes a book-local README plus section Markdown under
`wiki/books/<doc_id>/sections/`. The `markdown-graph` backend writes the same book
surface and adds deterministic concept wikilinks to section pages.

Required book output shape:

```text
wiki/books/<doc_id>/
  README.md
  sections/
    <section_id>.md
```

For the `markdown-graph` backend, section pages include a `## Linked concepts`
block with wiki-style links:

```text
- [[schema-evolution|Schema Evolution]]
```

The backend is intentionally stateless and book-local. It does **not** materialize
or reconcile `wiki/concepts/<concept_slug>.md`, does not store hidden backlink
state in Markdown, and does not own cross-book concept joins. Cross-book concept
nodes/mentions/backlinks belong to the index/query layer: the `concept_mentions`
table and `concept_nodes` view in `indexes/bookgraph.db`, from which the
`wiki/concepts/<concept_slug>.md` pages below are rendered by `bookgraph index
concepts` (see `index.md` for the schema and `commands.md` for the command).

Concept extraction is intentionally local and deterministic: no LLMs, embeddings,
or external services. It uses section titles, heading paths, title-case phrases,
and long domain-looking terms from a document's section text only. This extractor
lives in a shared module (`bookgraph.concepts`: `extract_concepts` + `ConceptEntry`)
used by **both** the `markdown-graph` wiki backend (for in-page wikilinks) and the
index stage (for `concept_mentions`), so the same slug/title is produced on both
sides. `sections.jsonl` is the single input; neither side reads the other's output.

## `wiki/concepts/<concept_slug>.md`

Owner: the index stage (`bookgraph index concepts` command / `bookgraph.index`).

One Markdown page per distinct concept, aggregated **across every indexed book**
and rendered from `indexes/bookgraph.db` (`concept_nodes` + `concept_mentions`).
This is the cross-book counterpart to the in-page `## Linked concepts` wikilinks
the `markdown-graph` backend emits: those link *to* `[[<slug>|Title]]`; this page
*is* that target and lists the backlinks.

```text
wiki/concepts/
  <concept_slug>.md      # e.g. schema-evolution.md
```

Page shape — a title, a one-line summary, then backlinks grouped by book in
reading order. A backlink shows its per-mention `gloss` after an em dash when present,
and an `(agent-verified)` marker when the mention came from a Tier-2 agent annotation
(`source='agent'`). When the mentioning section has a Tier-2 `summary`, it is rendered
beneath the backlink as an indented blockquote, so the page reads as a long-form,
provenance-aware concept note (each summary stays under the section it came from) rather
than only a list of glosses:

```markdown
# Schema Evolution

Mentioned in 2 books · 5 sections.

## Deep Work
- [Storage](../books/deep-work/sections/deep-work.a.md)

## Designing Data-Intensive Applications
- [Encoding and Evolution](../books/ddia/sections/ddia.ch-4.md) — why it matters here (agent-verified)
  > Schemas change over time; readers and writers must tolerate both older and newer
  > shapes, which is what backward/forward compatibility formalises.
```

Properties:

- **Derived and fully rebuildable** — never a source of truth. `bookgraph index
  concepts` rewrites the whole `wiki/concepts/` directory from the database, so it
  reflects exactly the concepts of the currently indexed documents.
- **Cross-book, so not per-document**: unlike `index build <doc_id>` (which is
  per-doc), concept pages need every book's mentions, so they are (re)rendered by
  the separate global `index concepts` pass, run after the relevant books are
  built. Concept *data* (`concept_mentions`) is still populated per-doc by `index
  build`; only the page rendering is global.
- `<concept_slug>` is the deterministic slug from `bookgraph.concepts`
  (`[a-z0-9]+(?:-[a-z0-9]+)*`), matching the `[[<slug>|…]]` targets in book pages.

## `indexes/bookgraph.db`

Owner: the index stage (`bookgraph index build` command / `bookgraph.indexes`).

The search index and the structural graph are compiled into **one workspace-wide
SQLite database** (`sections_fts` FTS5 + `section_graph` + `doc_catalog`), not
per-document JSON. It is a derived, fully rebuildable artifact — the canonical
source of truth stays in `sources/sections/<doc_id>/`. See **`index.md`** for the
full schema, build, query, and fallback contract.

> Supersedes the earlier `indexes/sections/<doc_id>.json` (inverted index) and
> `indexes/graph/<doc_id>.json` (structural graph) files, which are removed once a
> document is built into the database.

## `reading_plans/<plan_id>.json`

Owner: the reading-plan stage (`bookgraph reading-plan` commands /
`bookgraph.reading_plans`).

Daily reading progression state for one document. One JSON file per plan id,
mirroring `bookgraph.models.ReadingPlan`:

```json
{
  "plan_id": "daily-ddia",
  "doc_id": "ddia",
  "daily_sections": 2,
  "section_ids": ["ddia.intro", "ddia.chapter-1", "ddia.chapter-2"],
  "completed": ["ddia.intro"]
}
```

### Field rules

- `plan_id`: reading plan id; doubles as the filename, so it is a filesystem-safe
  slug (lowercase a-z, 0-9, hyphens). Validated on create.
- `doc_id`: the segmented document this plan reads.
- `daily_sections`: sections returned per `reading-plan next` tick. At least `1`.
- `section_ids`: the document's sections in linear reading order, copied from the
  `sources/sections/<doc_id>/sections.jsonl` line order at create time.
- `completed`: section ids marked read, in the order they were completed. A subset
  of `section_ids`.

### Derived state (not stored)

- **next batch**: the first up-to-`daily_sections` ids in `section_ids` that are
  not in `completed`, in reading order.
- **done**: every id in `section_ids` is in `completed`.

These are recomputed from `section_ids` + `completed` on each `next` call rather
than persisted, so the file stays a minimal source of truth.

### Write rules

- Every writer (`reading-plan create`/`mark-read`, the MCP `create_plan`,
  `mark_read`, and `complete_reading_batch` tools) replaces the file atomically
  (fsynced temp file in `reading_plans/` + rename), so a crash or power loss
  mid-write leaves the previous plan, never a truncated one. The file keeps its
  existing permissions (umask default on first write).
- Every writer holds a per-plan lock across load → modify → write (a CLI `create`,
  which overwrites blindly, across its write): an in-process lock plus, on POSIX, an
  advisory `flock` on `reading_plans/.<plan_id>.json.lock`, opened read-write (an exclusive
  lock on NFS needs it) and read-only only when the file is not writable to us, so a
  lock file created by another user does not block. Concurrent writers — e.g. the CLI and
  an MCP server — therefore never lose each other's updates. The `.lock` file is
  empty and may be left in place; it is not a plan. Without `fcntl` (Windows) only
  the in-process lock applies.
- `complete_reading_batch` appends a whole batch to `completed` in one write, and
  only after its readiness checks pass (see `commands.md`, *Reading batch
  completion*).

## `annotations/<doc_id>/<section_id>.json`

Owner: the MCP `annotate_section` tool (`bookgraph.mcp.service` /
`bookgraph.annotations`). Read by the index stage (`bookgraph index build`).

A **Tier-2 source of truth** — an agent's authoritative concepts + summary for one
section. Unlike `indexes/bookgraph.db` (derived), it is **not** rebuildable and ranks
alongside `sources/sections/<doc_id>/sections.jsonl`; `index build` reads it and never
writes it. One file per annotated section, mirroring
`bookgraph.models.SectionAnnotation`:

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

The concept edges here are the authoritative set for that section and, on the next
`index build`, override the deterministic Tier-1 extraction (an empty `concepts` list
prunes that section's mentions). See **`annotations.md`** for the field rules, the
presence-based merge rule, and the `markdown-graph` non-goal.

## `concepts/registry.json`

Owner: the `bookgraph concepts` CLI (`bookgraph.concept_registry`). Read by
`index build`, `index concepts`, and the MCP `get_concept` / `concept_hygiene` tools.

A **human-curated source of truth**, like `annotations/`: canonical concepts with
their deprecated aliases, ignored slugs, and reviewed-distinct pairs. A missing file is
an empty registry. An invalid one is an error. `index build` rewrites alias edges to
their canonical slug (keeping the original in `concept_mentions.raw_slug`) and drops
ignored slugs.

```json
{
  "canonical": [
    {"slug": "table-metadata", "title": "Table Metadata", "aliases": ["metadata-file"], "note": ""}
  ],
  "ignored": ["however"],
  "distinct": [["snapshot", "snapshot-id"]]
}
```

See **`concepts.md`** for the invariants, canonicalization rules, and hygiene reports.

## `translations/<lang>/<doc_id>/<section_id>.md` + `.json`

Owner: the translation registry, `bookgraph.translations` (written by the MCP
`write_section_translation` tool). Read by `get_section_translation`,
`list_section_artifacts`, reading-batch completion, and — read-only —
`bookgraph export translated-pdf`. The registry is the **only** location for
translations: `translation_cache/` is not read by anything (see below).

A **cache of generated per-section translations** plus its registry. The `.md` body
is the translation itself and keeps the path convention reading jobs already used;
the `.json` sidecar beside it is the registry record, mirroring
`bookgraph.models.SectionArtifact`:

```json
{
  "type": "translation",
  "lang": "vi",
  "doc_id": "iceberg",
  "section_id": "iceberg.table-format",
  "path": "translations/vi/iceberg/iceberg.table-format.md",
  "source_section_hash": "sha256:9f2c...",
  "content_hash": "sha256:41ab...",
  "includes_assets": true,
  "model": "claude-...",
  "created_at": "2026-10-02T00:00:00+00:00"
}
```

- `lang`: a lowercase hyphenated tag (`vi`, `pt-br`); input is lowercased, so
  `pt-BR` and `pt-br` name the same cache. Directories are always lowercase: a
  mixed-case legacy directory (e.g. `translations/pt-BR/`) is not a valid `lang` and
  is ignored by lookups and listings on case-sensitive filesystems — rename it to
  lowercase to bring it into the registry.
- `path`: the body, relative to the workspace root.
- `source_section_hash`: `sha256:` over the section's `title` + `text` (canonical
  JSON) at write time. Ids, page spans, and block ids are excluded, so a re-segment
  that keeps the words keeps the translation fresh.
- `content_hash`: `sha256:` over the body's UTF-8 bytes at write time. It binds the
  sidecar to the body it describes: a body overwritten afterwards (a path-convention
  writer, a manual edit) no longer matches and reads as `untracked`.
- `includes_assets`: whether the writer carried the section's figures/tables into
  the translation (declared by the writer, not inferred).

Freshness is **derived, never stored**: each read recomputes the section's current
hash and compares it with the sidecar.

| `status` | Meaning |
| --- | --- |
| `fresh` | Body + matching sidecar; reuse as-is. |
| `stale` | Body + sidecar, but the section's content changed since. |
| `untracked` | Body with no valid sidecar for it (cached before the registry, a corrupt/misplaced sidecar, or a body whose hash no longer matches `content_hash`); freshness unknown. |
| `missing` | No body (a sidecar without a body is ignored). |
| `orphaned` | Body whose section (or whole document) no longer exists; reported by listing. |

**Reuse rule.** `fresh` means the translation matches the section's current *text*;
the hash does not cover assets, so a re-parse that newly stages a figure leaves a
prose-only translation `fresh`. Reuse a translation as-is only when `status` is
`fresh` **and** (`includes_assets` or not `section_has_assets`).

Write order: remove the previous sidecar, write the body, then write the new sidecar,
each via a same-directory temp file + fsync + rename (files take the process umask,
not `mkstemp`'s 0600). A crash or a racing writer between the steps leaves an
`untracked` body — never a sidecar describing a different body. A sidecar whose
`lang`/`doc_id`/`section_id` disagrees with its location is ignored. Like
annotations, the cache is **not** rebuildable from sources (regenerating it costs
model calls), so `index build`, `segment`, and `wiki` never delete it.

## Translation bodies as read by `bookgraph export translated-pdf`

The export is a **read-only consumer** of the registry above: it resolves each
section with `bookgraph.translations.translation_state` and renders the `.md` body
(the `.json` sidecar is provenance, never content). `--lang` is normalised the same
way the registry does it (`VI` → `vi`). Per section:

| Registry status | Export |
| --- | --- |
| `fresh` | Rendered. `freshness: "fresh"`. |
| `stale` | Rendered with a *Translation may be outdated* note (and a `(may be outdated)` TOC marker); `freshness: "stale"` + `translation_stale`. |
| `untracked` | Rendered with a *Translation status unknown — it may be outdated* note (and a `(not tracked)` TOC marker); `freshness: "untracked"` + `translation_untracked`. |
| `missing` | Untranslated: follows `--fallback`. |

Stale and untracked translations count as translated, so `--fallback fail` accepts
them. `--strict` refuses `translation_stale` (known outdated) but not
`translation_untracked` (freshness unknown, e.g. a hand-written body), matching
reading-batch completion. Completeness follows the registry's reuse rule: a
registered translation with `includes_assets: false` of a section that has
figures/tables is rendered but flagged `translation_missing_assets`, which `--strict`
also refuses.

`translation_cache/<doc_id>/<section_id>.<lang>.md` is **not read**. It was an
export-only fallback that no BookGraph command ever wrote, so there is no migration:
to keep such a file, move it to `translations/<lang>/<doc_id>/<section_id>.md` (it
then reads as `untracked`), or re-register it with `write_section_translation`.

- Optional leading `---` frontmatter. Only flat `key: value` lines are read. `title`
  is the translated section title used in the table of contents.
- If the body starts with a heading, that heading is the translated title. Artifact
  headings are shifted so the top one sits at the section's `level`. A body with no
  heading gets the frontmatter `title`, or the original title.
- Image links (`![caption](images/fig1.png)`) may point at workspace files, for
  example the parsed assets under `sources/parsed/<doc_id>/images/`. They may also be
  absolute paths inside the workspace, such as an MCP `AssetRef.path`. See the
  asset-handling rules in `commands.md`.
- An empty or unreadable (including non-UTF-8) body counts as untranslated and is
  reported (`translation_empty` / `translation_unreadable`).

## `exports/<doc_id>.<lang>-progress.pdf` / `-bilingual.pdf` + `.report.json`

Owner: `bookgraph export translated-pdf`. This is derived, reader-facing output and
can be regenerated at any time. The report (`bookgraph.exports.models.ExportReport`)
is written beside the export:

```json
{
  "doc_id": "ddia",
  "title": "Designing Data-Intensive Applications",
  "lang": "vi",
  "mode": "translated",
  "fallback": "original",
  "generated_at": "2026-10-02T00:00:00Z",
  "total_sections": 3,
  "translated_sections": 1,
  "original_sections": 2,
  "skipped_sections": 0,
  "unpaired_sections": 0,
  "assets_missing": 1,
  "coverage": 0.3333,
  "sections": [
    {"section_id": "ddia.chapter-1", "title": "Chương 1", "level": 1,
     "source": "translated", "artifact": "translations/vi/ddia/ddia.chapter-1.md",
     "freshness": "fresh", "assets_embedded": 1, "assets_missing": 0,
     "original_assets_embedded": null, "original_assets_missing": null}
  ],
  "warnings": [
    {"code": "asset_missing", "message": "…", "section_id": "ddia.scalability",
     "reference": "t1.png"}
  ],
  "renderer": "playwright",
  "output": "/path/to/workspace/exports/ddia.vi-progress.pdf"
}
```

- `mode` is `translated` or `bilingual` (`--mode`).
- `source` is `translated`, `original`, or `skipped`: how the section's mixed
  rendering was filled (the right column in `bilingual` mode).
- `original_sections` / `skipped_sections` count the sections that took the
  `--fallback` path. `unpaired_sections` counts `bilingual` rows with no translation
  to compare against (always `0` in `translated` mode).
- `assets_embedded` / `assets_missing` count the mixed rendering;
  `original_assets_embedded` / `original_assets_missing` count the `bilingual` left
  column (`null` in `translated` mode). The top-level `assets_missing` counts the
  asset references that could not be embedded, per column. A missing original asset
  shown in both columns of an untranslated row is counted twice there but warned
  about once.
- `freshness` is the translation's registry status (`fresh`, `stale`, or
  `untracked`) for a `translated` section, `null` otherwise.
- `generated_at` follows `SOURCE_DATE_EPOCH` when it is set. With unchanged inputs and
  a pinned timestamp, the assembled HTML is byte-identical.
- Stable warning codes:
  - `asset_missing`, `asset_remote`, `asset_unsupported`: an asset is not in the
    export. `--strict` refuses these.
  - `translation_stale`: the source section changed after the translation was
    registered. Rendered with a note; `--strict` refuses it.
  - `translation_missing_assets`: a registered prose-only translation
    (`includes_assets: false`) of a section that has figures/tables. `--strict`
    refuses it.
  - `translation_untracked`: no valid registry record, or the body was edited after
    registration. Rendered with a note; never refused.
  - `translation_empty`, `translation_unreadable`: the section falls back.
  - `asset_captions_only` / `asset_text_sparse`: ingest quality warnings, passed
    through for rendered sections whose source prose is mostly captions.

## Future artifacts

Do not implement these without updating this file.
