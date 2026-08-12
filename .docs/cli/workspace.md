# Workspace Contract

Every CLI command that writes files must write below a single explicit workspace/output root.

## Workspace root

The workspace root is passed as either:

```bash
bookgraph init /path/to/workspace
bookgraph init --output /path/to/workspace
bookgraph parse source.md --output /path/to/workspace
```

For commands that already have a positional workspace argument, use that positional path:

```bash
bookgraph add-book /path/to/workspace /path/to/book.pdf
bookgraph paths /path/to/workspace
```

Commands should expand `~` and resolve to absolute paths before writing manifests.

## Directory layout

A valid initialized workspace contains:

```text
workspace/
  bookgraph.toml
  sources/
    inbox/
    parsed/
    sections/
  wiki/
    concepts/
    comparisons/
    daily/
  indexes/
  reading_plans/
  runs/
```

## Path meanings

| Path | Owner | Meaning |
| --- | --- | --- |
| `bookgraph.toml` | CLI config | Workspace defaults; must not contain secrets. |
| `sources/inbox/<book_id>/` | registration / ingestion | Raw source files and `book.json` registration manifest. |
| `sources/parsed/<doc_id>/` | parser stage | Canonical parser outputs, especially `document.json`; optional parser side artifacts. |
| `sources/sections/<doc_id>/` | segmenter stage | Human reading sections and section manifests. |
| `wiki/` | wiki backend | Compiled linked Markdown/wiki artifacts. |
| `indexes/` | index/search/graph stage | Deterministic indexes, graph DB files, search indexes. |
| `reading_plans/` | reading plan stage | Progress state for daily reading. |
| `runs/` | orchestration | Run logs and reproducibility metadata. |

## ID rules

- `book_id`: stable slug for a registered book source.
- `doc_id`: stable slug for a parsed canonical document.
- For a registered book, parser output should prefer the neighbouring `book.json` `book_id` as `doc_id`, unless the user passes an explicit `--doc-id`.
- Slugs are lowercase, ASCII-ish, hyphen-separated, and must not contain path separators.

## Mutation rules

- Commands must create parent directories as needed for their own output path.
- Commands must not delete unrelated artifacts.
- Commands must not rewrite another stage's outputs unless the command contract explicitly says so.
- `add-book` writes only `sources/inbox/<book_id>/...`.
- `parse` writes only `sources/parsed/<doc_id>/...` plus parser side artifacts inside that same parsed directory.
