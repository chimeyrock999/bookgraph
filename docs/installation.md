# Installation

BookGraph is a Python package (`bookgraph`) with a CLI and an optional MCP server for
AI reading agents. Core installs stay light; heavyweight integrations such as MinerU,
MarkItDown, FastMCP, WeasyPrint, and Playwright are optional extras.

## Requirements

- **Python 3.11+**.
- **uv** is recommended for source installs and development, but `pip`/`pipx` work.
- **MinerU 4** is needed only for raw-PDF parsing with `bookgraph parse-book`.
  The `flash` tier uses the PDF text layer only; `basic`, `standard`, and `advanced`
  may download models on first run.

## Install from a release

Each GitHub release ships a wheel and source distribution.

```bash
# Download the latest wheel into the current directory
gh release download --repo chimeyrock999/bookgraph --pattern '*.whl'

# Core CLI only
python -m pip install ./bookgraph-*.whl

# Common reading-agent setup: MCP server + raw-PDF parsing
WHEEL=$(ls bookgraph-*.whl)
python -m pip install "${WHEEL}[mcp,mineru]"
```

Run the wheel without creating a project environment:

```bash
uvx --from "$(ls bookgraph-*.whl)" bookgraph --help
```

Prefer a specific version? List releases with:

```bash
gh release list --repo chimeyrock999/bookgraph
```

Then pass a tag to `gh release download`, or install a wheel URL directly:

```bash
python -m pip install "bookgraph[mcp] @ <wheel-url>"
```

## Install from source

Use this for development or unreleased changes:

```bash
git clone https://github.com/chimeyrock999/bookgraph.git
cd bookgraph

uv run bookgraph --help        # resolves the locked environment on demand
uv sync                        # optional persistent environment, core deps only
uv run bookgraph paths /path/to/workspace
```

Editable pip install:

```bash
python -m pip install -e .
python -m pip install -e ".[mcp,mineru]"
bookgraph --help
```

One-off install from Git:

```bash
uvx --from git+https://github.com/chimeyrock999/bookgraph.git bookgraph --help
```

## Optional extras

Extras are declared in `pyproject.toml`. With uv, add them to `uv sync` or `uv run`
with `--extra`.

| Extra | Enables | Install example |
| --- | --- | --- |
| `parsers` | MarkItDown + pypdf adapters for Office/HTML/simple-PDF inputs | `uv sync --extra parsers` |
| `mineru` | MinerU 4 raw-PDF parsing via `bookgraph parse-book` | `uv sync --extra mineru` |
| `mineru-torch` | MinerU Torch stack for GPU-backed model setups | `uv sync --extra mineru-torch` |
| `mcp` | FastMCP server exposed by `bookgraph mcp` | `uv sync --extra mcp` |
| `pdf` | WeasyPrint renderer for translated PDF export; may require system Pango | `uv sync --extra pdf` |
| `pdf-chromium` | Playwright/Chromium renderer for translated PDF export | `uv sync --extra pdf-chromium && uv run playwright install chromium` |
| `dev` | pytest, ruff, mypy, and contributor tooling | `uv sync --extra dev` |

Combine extras as needed:

```bash
uv sync --extra mcp --extra mineru --extra pdf-chromium
```

You do **not** need MinerU to read an already parsed workspace, serve MCP, parse
Markdown, or parse Office/HTML through MarkItDown.

## MinerU 4 notes

`bookgraph parse-book` runs `mineru-kit parse` on a registered raw source, stages the
result bundle under the workspace, then converts MinerU middle JSON to BookGraph's
canonical `document.json`.

MinerU 3.x is not supported. The old `mineru -p ... -b <backend>` CLI, the `pipeline`
extra, and `mineru.json` do not apply.

Parse quality is selected by `--tier` or a profile:

| Tier | What runs |
| --- | --- |
| `flash` | PDF text layer only; fast for born-digital PDFs, poor for scans |
| `basic` | Small layout/OCR models on CPU; default tier |
| `standard` | Adds a VLM, local or remote MinerU V1 service with `--url` |
| `advanced` | Highest-effort VLM tier |

Download models before a long run if you do not want first-run downloads in the parse:

```bash
uv run mineru-kit models download --tier basic
```

MinerU reads settings from `$MINERU_HOME/config.yaml`; override with `MINERU_CONFIG`.
A remote MinerU V1 service reads its API key from `MINERU_API_KEY`, so the key stays
out of the `parse-book` command line and run log.

Long raw-PDF run guide: [`cli/parse-book-large-pdfs.md`](cli/parse-book-large-pdfs.md).

## Verify the install

```bash
bookgraph --help          # or: uv run bookgraph --help
bookgraph parsers         # lists parser plugins
```

Verify optional surfaces:

```bash
uv run --extra mcp bookgraph mcp --help
uv run --extra dev pytest -q
uv run --extra dev ruff check .
uv run --extra dev mypy src/bookgraph
```

## Next steps

- [Project quickstart](../README.md#quickstart) — initialize a workspace and run the
  core pipeline.
- [`cli/`](cli/) — full command and artifact contracts.
- [`mcp/reading-agent.md`](mcp/reading-agent.md) — connect an MCP client and read
  section by section.
