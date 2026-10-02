# Installation

BookGraph is a Python package (`bookgraph`) that ships a `bookgraph` CLI and an
optional MCP server. Heavy integrations (MinerU, MarkItDown, FastMCP) are **optional
extras** — install only what your workflow needs.

## Requirements

- **Python ≥ 3.11** (declared in `pyproject.toml`).
- **[uv](https://docs.astral.sh/uv/)** — the recommended toolchain for running,
  syncing extras, and building. `pip`/`pipx` work too (see below).
- For raw-PDF parsing only: **MinerU 4** (`mineru>=4.0.10,<5`, Python < 3.15). Its
  `flash` tier needs no models; the other tiers download models on first run — see
  [Optional extras](#optional-extras), [MinerU 4](#mineru-4) and
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
| `mineru` | MinerU 4 for raw-PDF parsing (`bookgraph parse-book`): every tier, small models on ONNX and the VLM on llama.cpp | `uv sync --extra mineru` |
| `mineru-torch` | `mineru` plus the Torch stack (`mineru[torch]`) for GPU-backed models | `uv sync --extra mineru-torch` |
| `mcp` | FastMCP server (`bookgraph mcp`) that serves an agent | `uv sync --extra mcp` |
| `pdf` | WeasyPrint PDF renderer for `bookgraph export translated-pdf` (needs system Pango) | `uv sync --extra pdf` |
| `pdf-chromium` | Playwright/Chromium PDF renderer for `bookgraph export translated-pdf` | `uv sync --extra pdf-chromium && uv run playwright install chromium` |
| `dev` | pytest, ruff, mypy for contributing | `uv sync --extra dev` |

Combine extras as needed, e.g. a reading-agent setup that also parses raw PDFs:

```bash
uv sync --extra mineru --extra mcp
```

> **MinerU note:** the `mineru` extras are intentionally optional — you do not need
> them to read an already-parsed workspace or to parse Markdown/Office sources. See
> [`cli/parse-book-large-pdfs.md`](cli/parse-book-large-pdfs.md) for model caches,
> logs, and diagnosis of long parses.

### MinerU 4

`bookgraph parse-book` runs `mineru-kit parse` (MinerU 4). MinerU 3.x is no longer
supported: its `mineru -p … -b <backend>` CLI, the `pipeline` extra, and
`mineru.json` are gone in 4.0. Pick the parse quality with a **tier** (through a
`--profile` or `--tier`):

| Tier | What runs |
|------|-----------|
| `flash` | PDF text layer only, no models — fast for born-digital books, nothing for scans |
| `basic` | small layout/OCR models on CPU, OCR when a page needs it (the default) |
| `standard` | adds a VLM — local, or a remote MinerU V1 service with `--url` |
| `advanced` | the VLM at its highest effort |

The base `mineru` extra runs every tier: small models on ONNX and the VLM on
llama.cpp, CPU included. `mineru-torch` adds Torch for GPU-backed models; vLLM /
LMDeploy engines need MinerU's own `mineru[full]`.

Download the models for a tier ahead of a long run (otherwise the first parse does it):

```bash
uv run mineru-kit models download --tier basic
```

MinerU 4 reads its own settings from `$MINERU_HOME/config.yaml` (override the path
with `MINERU_CONFIG`). A remote MinerU V1 parse service reads its API key from
`MINERU_API_KEY`, so the key never appears in the `parse-book` argv or run log.

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
