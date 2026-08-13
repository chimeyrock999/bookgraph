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

## `indexes/sections/<doc_id>.json`

Owner: the index stage (`bookgraph index build` command /
`bookgraph.indexes`).

Per-document inverted index that backs MCP `search`. Mirrors
`bookgraph.indexes.SectionIndex`:

```json
{
  "doc_id": "ddia",
  "sections": [
    {"id": "ddia.chapter-3-storage", "title": "Chapter 3. Storage", "text": "Opening paragraph."}
  ],
  "postings": {
    "storage": {"ddia.chapter-3-storage": 2},
    "opening": {"ddia.chapter-3-storage": 1}
  }
}
```

### Field rules

- `doc_id`: parent document id; matches the `sources/sections/<doc_id>/` folder
  and the `<doc_id>.json` filename.
- `sections`: one entry per indexed section, in reading order, holding the `id`,
  `title`, and `text` — a denormalised copy carried purely so `search` can build
  result snippets without re-reading `sections.jsonl`.
- `postings`: `token -> {section_id: term frequency}`. Tokens are lowercased
  maximal `[a-z0-9]+` runs from each section's `title` + `text`; the same
  tokenizer scores the live-scan fallback, so an unindexed document ranks
  identically.

Fully regenerable from `sections.jsonl`; safe to delete and rebuild. Rebuild
after re-segmenting a document so the denormalised text stays in sync.

## `indexes/graph/<doc_id>.json`

Owner: the index stage (`bookgraph index build` command / `bookgraph.graph`).

Per-document structural graph that backs the graph/context MCP tools. Mirrors
`bookgraph.graph.SectionGraph`:

```json
{
  "doc_id": "ddia",
  "nodes": [
    {
      "id": "ddia.part-i",
      "title": "Part I",
      "level": 1,
      "heading_path": ["Part I"],
      "parent_id": null,
      "prev_id": null,
      "next_id": "ddia.chapter-1",
      "child_ids": ["ddia.chapter-1"]
    }
  ]
}
```

### Field rules

- `doc_id`: parent document id; matches the `sources/sections/<doc_id>/` folder
  and the `<doc_id>.json` filename.
- `nodes`: one entry per section, in reading order.
- `parent_id`: the nearest preceding section with a smaller heading `level` (the
  containing chapter/part), or `null` for a top-level section. Derived from
  `level` via a reading-order stack, so a jump from level 1 to level 3 still
  nests under the nearest shallower section.
- `child_ids`: the inverse of `parent_id` — direct children in reading order.
- `prev_id` / `next_id`: linear reading-order neighbours, carried through from the
  sections manifest; a neighbour id not present in this document is dropped to
  `null`.

Two edge kinds are represented: **hierarchy** (`parent_id` / `child_ids`) and
**sequence** (`prev_id` / `next_id`). Fully regenerable from `sections.jsonl`;
built from the same manifest as, and alongside, the search index. Rebuild after
re-segmenting a document.

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

## Future artifacts

Do not implement these without updating this file.
