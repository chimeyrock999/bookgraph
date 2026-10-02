# Operating `parse-book` on large PDFs

This guide is for users and agent operators running BookGraph on large registered
PDFs. It focuses on how to run and diagnose the command, not on the internal
runner design.

## Basic command

```bash
bookgraph parse-book /path/to/workspace <book_id>
```

For the MinerU runner, first-time runs of the model tiers (`basic`, `standard`,
`advanced`) may download large model files and can take a long time on books with
hundreds of pages. For example, a 552-page PDF can run long enough that it should
not be launched from a cron job without a log path.

For a born-digital book, the `flash` tier (`--profile fast-text`) reads the PDF text
layer with no models and is the fastest way to a `document.json`. It extracts
nothing from scanned pages; use `basic` (the default) or higher for those. A page
range (`--start-page` / `--end-page`, 0-based) parses a slice, e.g. to check quality
before a full run.

## Progress and logs

`parse-book` prints the run log before MinerU starts:

```text
runner: mineru
book_id: iceberg-defitive-guide
pages: 552
log: /path/to/workspace/runs/parse-book/20260813T120000Z-iceberg-defitive-guide.log
stage: running MinerU
```

Tail that log from another terminal or agent step:

```bash
tail -f /path/to/workspace/runs/parse-book/<timestamp>-<book_id>.log
```

The log includes the invoked command, runtime notes such as `HF_HOME`, streamed
MinerU output, process exit code, and a final parse artifact summary.

## HuggingFace cache permissions

If HuggingFace/MinerU fails with a permission error under `~/.cache`, set a
workspace-local cache directory:

```bash
mkdir -p /path/to/workspace/runs/huggingface-cache
HF_HOME=/path/to/workspace/runs/huggingface-cache \
  bookgraph parse-book /path/to/workspace <book_id>
```

This keeps model downloads under the BookGraph workspace's `runs/` tree instead
of relying on a user-global cache that may be owned by another process/user.

## MinerU dependencies

The `mineru` extra runs every tier on CPU (small models on ONNX, the `standard` /
`advanced` VLM on llama.cpp). The VLM tiers are slow on CPU; the `mineru-torch` extra
adds Torch for GPU-backed models:

```bash
uv sync --extra mineru         # every tier, CPU
uv sync --extra mineru-torch   # + Torch for GPU-backed models
```

Pre-download a tier's models so the parse itself does not stall on the download:

```bash
uv run mineru-kit models download --tier basic
```

A MinerU 3.x setting fails before MinerU starts, with the replacement to use:
`--backend`/`[mineru].backend` names the tier that replaced it, `--effort`,
`--formula` and `--table` are decided by the tier, and `command = "mineru"` must
become `mineru-kit`.

## MarkItDown PDF fallback

`markitdown` can be used for simple document conversion, but PDF support requires
its optional PDF dependencies (`markitdown[pdf]` or `markitdown[all]`). The
BookGraph `parsers` extra is not a guaranteed PDF fallback for raw PDF books.
Prefer the registered-book `parse-book` flow for raw PDFs.

## Agent / cron behavior

A reading agent should check prepared artifacts before trying to read:

```text
sources/parsed/<book_id>/document.json
sources/sections/<book_id>/sections.jsonl
indexes/bookgraph.db
reading_plans/<book_id>.json
```

If `document.json` is missing, the job should either report that a heavy parse is
needed (including the intended command/log path) or start a tracked parse that has
a durable log. It should not silently wait forever with no progress output.
