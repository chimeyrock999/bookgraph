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

## Install from a release (recommended)

Each [GitHub release](https://github.com/chimeyrock999/bookgraph/releases) ships a
built wheel and sdist, so you can install BookGraph without cloning the repo. The `gh`
CLI grabs the latest release by default, so nothing here pins a version:

```bash
# Download the latest release wheel into the current directory:
gh release download --repo chimeyrock999/bookgraph --pattern '*.whl'

# Install it — core only:
python -m pip install ./bookgraph-*.whl

# …or with extras (MCP server + raw-PDF parsing):
WHEEL=$(ls bookgraph-*.whl)
python -m pip install "${WHEEL}[mcp,mineru]"
```

Run it as a one-off isolated tool with uv (no environment to manage):

```bash
uvx --from "$(ls bookgraph-*.whl)" bookgraph --help
```

> Prefer a specific version? `gh release list --repo chimeyrock999/bookgraph` lists the
> tags; pass one to `gh release download <tag> …`, or copy a wheel URL from the releases
> page and `pip install "bookgraph[mcp] @ <wheel-url>"`.

## Install from source

For development, or to run an unreleased revision, work from a clone. `uv run
bookgraph …` runs against the locked project environment with no activation step:

```bash
git clone https://github.com/chimeyrock999/bookgraph.git
cd bookgraph

uv run bookgraph --help      # run the CLI (uv resolves deps on demand)

# …or create a persistent project environment:
uv sync                      # core dependencies only
uv run bookgraph paths /path/to/workspace
```

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

- [Quickstart](../README.md#quickstart) — `bookgraph init`, `add-book`, `parse`, and
  the canonical output paths.
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
