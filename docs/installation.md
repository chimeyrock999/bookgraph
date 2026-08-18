# Installation

BookGraph is a Python package (`bookgraph`) that ships a `bookgraph` CLI and an
optional MCP server. Heavy integrations (MinerU, MarkItDown, FastMCP) are **optional
extras** — install only what your workflow needs.

## Requirements

- **Python ≥ 3.11** (declared in `pyproject.toml`).
- **[uv](https://docs.astral.sh/uv/)** — the recommended toolchain for running,
  syncing extras, and building. `pip`/`pipx` work too (see below).
- For raw-PDF parsing only: **MinerU**, which pulls in ML dependencies and downloads
  models on first run — see [Optional extras](#optional-extras) and
  [`cli/parse-book-large-pdfs.md`](cli/parse-book-large-pdfs.md).

## Install from source (recommended)

```bash
git clone https://github.com/chimeyrock999/bookgraph.git
cd bookgraph

# Run the CLI without a manual install — uv resolves the environment on demand:
uv run bookgraph --help

# …or create a persistent project environment:
uv sync                      # core dependencies only
uv run bookgraph paths /path/to/workspace
```

`uv run bookgraph …` is the primary entry point used throughout the docs. It always
runs against the locked project environment, so no separate activation step is needed.

## Optional extras

Extras are declared in `pyproject.toml` under `[project.optional-dependencies]`. Add
them to `uv sync` (persistent) or `uv run` (one-off) with `--extra`:

| Extra | Enables | Install |
|-------|---------|---------|
| `parsers` | MarkItDown + pypdf adapters for Office/HTML/simple-PDF → Markdown | `uv sync --extra parsers` |
| `mineru` | MinerU pipeline for raw-PDF layout parsing (`bookgraph parse-book`) | `uv sync --extra mineru` |
| `mcp` | FastMCP server (`bookgraph mcp`) that serves an agent | `uv sync --extra mcp` |
| `dev` | pytest, ruff, mypy for contributing | `uv sync --extra dev` |

Combine extras as needed, e.g. a reading-agent setup that also parses raw PDFs:

```bash
uv sync --extra mineru --extra mcp
```

> **MinerU note:** the `mineru` extra installs a large ML stack and downloads models
> on first parse. It is intentionally optional — you do not need it to read an
> already-parsed workspace or to parse Markdown/Office sources. See
> [`cli/parse-book-large-pdfs.md`](cli/parse-book-large-pdfs.md) for model caches,
> logs, and diagnosis of long parses.

## Install with pip / pipx

If you prefer pip, install the package (editable, from a clone) with the same extras:

```bash
python -m pip install -e .                 # core
python -m pip install -e ".[mcp,mineru]"   # with extras
bookgraph --help
```

Or run the CLI in an isolated, throwaway environment without cloning via uv:

```bash
uvx --from git+https://github.com/chimeyrock999/bookgraph.git bookgraph --help
```

## Verify the install

```bash
bookgraph --help          # or: uv run bookgraph --help
bookgraph parsers         # lists available parser plugins
```

To confirm the MCP extra is wired up:

```bash
uv run --extra mcp bookgraph mcp --help
```

## Next steps

- [Workspace layout](../README.md#workspace-layout) — `bookgraph init`, `add-book`,
  `parse`, and the canonical output paths.
- [`mcp/reading-agent.md`](mcp/reading-agent.md) — point an MCP client at a workspace
  and read section by section.
- [`cli/`](cli/) — full CLI command and artifact contracts.

## Development install

For contributing, sync the dev extra and run the checks (see also the
[Development](../README.md#development) section):

```bash
uv sync --extra dev
uv run --extra dev pytest -q
uv run --extra dev ruff check .
uv run --extra dev mypy src/bookgraph
```
