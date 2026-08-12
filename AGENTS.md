# Project Instructions

BookGraph is a Python project for a pluggable source-grounded document/book graph wiki pipeline.

## Design principles

- Keep parser, segmenter, wiki backend, index, and MCP server implementations behind ports/protocols.
- Treat parser outputs as source evidence; downstream graph notes must preserve provenance.
- Segmenting means human reading units, not generic RAG chunks.
- Prefer deterministic structure first: PDF TOC/bookmarks, MinerU title blocks, Markdown headings, page/paragraph boundaries, token fallback.
- Do not make heavy tools mandatory. MinerU, MarkItDown, llm-wiki-compiler, and FastMCP should be adapters/extras where possible.

## Commands

- Tests: `uv run --extra dev pytest -q`
- Lint: `uv run --extra dev ruff check .`

## Style

- Python 3.11+.
- Use `pathlib.Path` for paths.
- Keep data contracts in `models.py` or dedicated model modules.
- Add tests for behavior before implementation.
