# BookGraph

> A source-grounded **book-to-graph pipeline for AI reading** — parse long books and
> documents into a durable knowledge graph an agent reads one section at a time.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Interface](https://img.shields.io/badge/interface-MCP-6E56CF)
![Packaging](https://img.shields.io/badge/packaging-uv-DE5FE9)

BookGraph takes long books and documents, parses them into a durable knowledge graph,
and lets an AI agent read that graph one section at a time. The agent keeps its place,
follows the book's outline, jumps to related sections, and connects recurring concepts
across many books without losing source provenance.

A BookGraph **workspace** is the source of truth. It stores inspectable artifacts for
every stage: parsed blocks, human-sized sections, linked wiki pages, search/graph
indexes, cross-book concept backlinks, and resumable reading plans served over MCP.

## Why BookGraph

BookGraph is **not just another RAG chunker**. It is built for studying and navigating
long-form sources, where provenance, reading order, outlines, and cross-book concepts
matter as much as keyword retrieval.

- **Artifact-first, not prompt-first** — every stage writes canonical files under
  the workspace (`document.json`, `sections.jsonl`, `indexes/bookgraph.db`,
  `reading_plans/*.json`, `wiki/`). The system is debuggable without rerunning an
  LLM call.
- **Human reading units, not arbitrary chunks** — segmentation prefers headings,
  PDF bookmarks/TOCs, page boundaries, and only then token fallback. Sections are
  meant to be read, discussed, and resumed.
- **Source-grounded by construction** — downstream notes, wiki pages, MCP answers,
  and reading plans preserve section/block provenance back to parser outputs.
- **Graph + reading plan, not only vector search** — BookGraph keeps outline
  structure, prev/next reading order, related sections, and cross-book concept
  backlinks alongside search.
- **Wiki and MCP are parallel projections** — the wiki is the human/external-LLM
  rendering; MCP serves agents from the canonical sections and indexes. Neither
  is the hidden source of truth.
- **Pluggable ports** — parsers, segmenters, wiki backends, index backends, and
  MCP serving are replaceable behind small interfaces, so heavy tools like
  MinerU, MarkItDown, llm-wiki-compiler, and FastMCP stay optional adapters.

## Installation

BookGraph needs **Python ≥ 3.11**. The recommended toolchain is
[uv](https://docs.astral.sh/uv/); heavy integrations (MinerU, MarkItDown, FastMCP)
are optional extras you add only when needed.

```bash
# Install the latest release wheel (no clone required):
gh release download --repo chimeyrock999/bookgraph --pattern '*.whl'
python -m pip install "$(ls bookgraph-*.whl)[mcp]"

# …or run from a clone with uv:
git clone https://github.com/chimeyrock999/bookgraph.git && cd bookgraph
uv run bookgraph --help
```

Extras: `parsers` (Office/HTML/simple-PDF), `mineru` (raw-PDF layout parsing),
`mcp` (FastMCP server), `dev` (pytest/ruff/mypy). The full guide — release, source,
pip/pipx, and the MinerU model download — is in
[`docs/installation.md`](docs/installation.md).

## Quickstart

Create a workspace (the canonical output directory) and inspect its paths:

```bash
bookgraph init /path/to/workspace       # or: bookgraph init --output /path/to/workspace
bookgraph paths /path/to/workspace
```

Register a raw PDF book (copies it to `sources/inbox/<book_id>/` and writes a
`book.json` registration manifest; add `--dry-run` to preview):

```bash
bookgraph add-book /path/to/workspace /path/to/book.pdf
```

Parse a source into canonical blocks — the parser is picked by file type, or forced
with `--parser`:

```bash
bookgraph parsers                                    # list parser plugins
bookgraph parse notes.md -o /path/to/workspace       # parser picked by file type
bookgraph parse book_middle.json -o /path/to/workspace
bookgraph parse report.docx -o /path/to/workspace    # needs: uv sync --extra parsers
bookgraph parse odd.bin -o /path/to/workspace --parser markdown
bookgraph parse-book /path/to/workspace <book_id> --backend pipeline  # needs: uv sync --extra mineru
# Large raw PDFs print a runs/parse-book/*.log path; see docs/cli/parse-book-large-pdfs.md
```

Output lands in `sources/parsed/<doc_id>/document.json`. `<doc_id>` comes from the
neighbouring `book.json` when the source sits in a registered book directory, from
`--doc-id`, or from the filename. `bookgraph parse-book` invokes MinerU on a
registered raw PDF, stages the generated middle JSON under `sources/parsed/<book_id>/`,
then writes `document.json` via the `mineru-middle-json` parser. Direct `bookgraph
parse` on a raw PDF still needs an explicit parser choice (e.g. `--parser markitdown`
for simple text PDFs).

A workspace holds these canonical paths:

```text
sources/inbox/       # original incoming files and per-book registration manifests
sources/parsed/      # parser outputs: .md, middle.json, layout.pdf, assets
sources/sections/    # section markdown generated by segmenters
wiki/                # compiled linked markdown wiki
indexes/             # graph/search indexes
reading_plans/       # daily reading progression state
runs/                # run logs/artifacts
bookgraph.toml       # workspace config
```

`bookgraph.toml` supplies workspace defaults for parser routing, MinerU runner
settings, segmenter selection/heading target level, wiki backend, and reading-plan
daily batch size. Explicit CLI flags still win over config defaults. Next, wire an
agent to the workspace — see [`docs/mcp/reading-agent.md`](docs/mcp/reading-agent.md).

## External tools

BookGraph keeps heavyweight integrations optional and wraps them behind local
ports/adapters:

- [MinerU](https://github.com/opendatalab/MinerU) — raw PDF parsing/layout
  extraction, staged as MinerU `*_middle.json` before conversion to `document.json`.
- [MarkItDown](https://github.com/microsoft/markitdown) — optional Office/HTML/etc.
  to Markdown conversion for the `markitdown` parser adapter.
- [llm-wiki-compiler](https://github.com/atomicstrata/llm-wiki-compiler) — optional
  wiki compiler target; BookGraph's `llmwiki` backend stages section Markdown for it.
- [FastMCP](https://github.com/jlowin/fastmcp) — optional MCP server framework used
  by `bookgraph mcp` when the `mcp` extra is installed.

## Architecture

The pipeline is a chain of pluggable stages behind small ports. Each stage writes
a canonical artifact the next stage reads, and the MCP server serves reading and
graph/context tools straight off the segmented sections plus the indexes.

```mermaid
flowchart TD
    docs["Input docs<br/>PDF · DOCX · HTML · Markdown · MinerU JSON"]

    docs --> P

    subgraph P["Parser plugins — bookgraph parse"]
        direction LR
        P1["mineru-middle-json"]
        P2["markitdown<br/>(optional extra)"]
        P3["markdown"]
    end

    P --> DOC["Canonical document<br/>sources/parsed/&lt;doc&gt;/document.json"]
    DOC --> S

    subgraph S["Segmenter plugins — bookgraph segment"]
        direction LR
        S1["heading"]
        S2["bookmark"]
        S3["token/page fallback"]
    end

    S --> SEC["Reading sections<br/>sources/sections/&lt;doc&gt;/*.jsonl + .md"]

    SEC --> IDX
    SEC --> RP["Reading plan<br/>reading_plans/&lt;plan&gt;.json"]
    SEC --> W

    subgraph IDX["Index stage — bookgraph index build"]
        direction LR
        IDX1["FTS5 search<br/>indexes/bookgraph.db"]
        IDX2["section graph<br/>indexes/bookgraph.db"]
        IDX3["concept graph<br/>indexes/bookgraph.db"]
    end

    subgraph W["Wiki backend plugins — bookgraph wiki compile"]
        direction LR
        W1["llmwiki staging"]
        W2["markdown graph backend"]
    end

    W --> WIKI["Linked wiki<br/>wiki/books/ + wikilinks"]
    IDX --> WIKIC["Concept pages<br/>wiki/concepts/<br/>(bookgraph index concepts)"]

    IDX --> MCP
    SEC --> MCP
    RP --> MCP

    subgraph MCP["MCP server — bookgraph mcp"]
        direction LR
        MCP1["reading<br/>get_next_section · get_section · mark_read"]
        MCP2["search"]
        MCP3["graph/context<br/>get_outline · get_related · get_context"]
        MCP4["concepts<br/>get_concept"]
    end

```

File type routing picks the parser adapter, and `--parser` overrides it.

The **wiki output** (`wiki/`) and the **MCP server** are two independent, parallel
consumers of the same upstream — the sections manifest and the index — not a chain.
MCP serves reading/query programmatically straight from `sources/sections/`,
`indexes/bookgraph.db`, and `reading_plans/`; it never reads the wiki files. The
wiki is the human / external-LLM-facing rendering (book pages via `wiki compile`,
cross-book concept pages via `index concepts`). The two stay consistent because
both derive concepts from the same shared extractor (`bookgraph.concepts`), not
because one reads the other.

## Module layout

```text
src/bookgraph/
  cli/                      # Typer CLI (one module per pipeline stage)
  books.py                  # CLI-only book registration contract
  workspace.py              # Workspace/output path contract
  models.py                 # CanonicalBlock, Document, Section, ReadingPlan
  ports.py                  # Parser / Segmenter / WikiBackend interfaces
  plugins.py                # Name-based plugin registry
  defaults.py               # Built-in plugin registrations
  documents.py              # document.json reader/writer
  sections.py               # sections.jsonl + <section_id>.md reader/writer
  pdf_metadata.py           # cheap PDF metadata/bookmark inspection
  reading_plans.py          # reading plan store (create/next/mark-read core)
  graph.py                  # section graph model + builder (hierarchy + sequence)
  concepts.py               # shared deterministic concept extractor (wiki + index)
  index/
    base.py                 # IndexBackend port + hits/concept models + tokenizer
    sqlite.py               # default backend: SQLite/FTS5 (indexes/bookgraph.db)
  parsers/
    mineru.py               # MinerU *_middle.json adapter
    markdown.py             # Markdown -> canonical blocks (shared by Markdown-producing parsers)
    markitdown.py           # MarkItDown adapter (lazy optional dependency)
    routing.py              # File type -> parser plugin name
  segmenters/
    heading.py              # Heading/title-block segmenter
    bookmark.py             # PDF bookmark/outline segmenter (heading fallback)
    token_page.py           # Token-budget/page-boundary fallback segmenter
  wiki_backends/
    llmwiki.py              # Stage section markdown for llm-wiki-compiler
    markdown_graph.py       # Linked markdown wiki: book pages + wikilinks (uses concepts.py)
  mcp/
    service.py              # Reading/query logic (FastMCP-free, unit-tested)
    server.py               # FastMCP server wrapper (optional `mcp` extra)
```

## Documentation

Public docs live in `docs/`:

- [`docs/installation.md`](docs/installation.md) — install BookGraph and its extras.
- [`docs/cli/`](docs/cli/) — user-facing CLI guides plus filesystem/command contracts.
- [`docs/mcp/`](docs/mcp/) — user-facing agent/MCP setup guides.
- [`docs/design/`](docs/design/) — maintainer-facing design notes, invariants, and
  runtime contracts.

Update the relevant doc on `main` before implementing or changing CLI/artifact
behavior so other agents can coordinate safely.

## Agent skills

BookGraph ships a `bookgraph-reader` skill in two forms:

- `.claude/skills/bookgraph-reader/SKILL.md` for Claude agents.
- `.agents/skills/bookgraph-reader/SKILL.md` as an agent-neutral procedure for
  Hermes/custom MCP clients or any agent runtime that can load `SKILL.md`.

Both describe the MCP reading loop over a prepared workspace.

## Development

```bash
uv sync --extra dev
uv run --extra dev pytest -q
uv run --extra dev ruff check .
uv run --extra dev mypy src/bookgraph
```

Build release distributions locally:

```bash
uv build
uvx twine check dist/*
```

GitHub Actions builds release artifacts with `.github/workflows/release.yml`:

- `workflow_dispatch` builds and uploads the `bookgraph-dist` artifact.
- pushing a `v*` tag builds the same artifacts and attaches them to a GitHub
  Release with generated release notes.
