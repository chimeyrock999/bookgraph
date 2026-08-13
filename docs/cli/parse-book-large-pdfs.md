# Operating `parse-book` on large PDFs

This guide is for users and agent operators running BookGraph on large registered
PDFs. It focuses on how to run and diagnose the command, not on the internal
runner design.

## Basic command

```bash
bookgraph parse-book /path/to/workspace <book_id>
```

For the MinerU runner, first-time runs may download large model files and can take
a long time on books with hundreds of pages. For example, a 552-page PDF can run
long enough that it should not be launched from a cron job without a log path.

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

The `mineru` extra includes the runtime dependency needed for the local VLM path:

```bash
uv sync --extra mineru
```

If you run ad hoc without syncing, ensure `accelerate` is present in that command
environment. Older environments may need:

```bash
uv run --with accelerate --extra mineru bookgraph parse-book /path/to/workspace <book_id>
```

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
