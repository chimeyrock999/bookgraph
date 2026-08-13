# `parse-book` runtime design

This note is for maintainers and implementation agents. User-facing operational
guidance lives in `docs/cli/parse-book-large-pdfs.md`.

## Problem

`bookgraph parse-book` invokes heavy external PDF tooling. MinerU can run for a
long time, download large models on first use, and emit useful progress while it
works. Capturing subprocess output until exit makes the CLI look hung to users and
makes agent/cron jobs hard to diagnose.

## Runtime contract

The default CLI path should:

1. create a per-run log before the external runner starts;
2. print the log path immediately;
3. stream the runner's combined stdout/stderr to both stderr and the log;
4. preserve enough output in the raised error to keep Typer errors actionable;
5. append a final parse artifact summary on both success and failure;
6. keep unit-test seams that do not require real MinerU.

The current log location is:

```text
runs/parse-book/<timestamp>-<book_id>.log
```

The log starts with BookGraph runtime metadata, then the invoked command, streamed
MinerU output, process exit code, and final artifact state.

## Runner seam

`MinerURunner.run_process` remains the unit-test seam. When injected, tests can
return a `subprocess.CompletedProcess` without spawning real MinerU. When not
injected and `log_path` is set, the default runner uses a streaming subprocess
implementation that tees output to the log and terminal.

This keeps fast tests deterministic while making the real CLI user-friendly.

## Timeout and cancellation

Timeouts should kill the child process, wait for it to exit, and raise
`subprocess.TimeoutExpired`, which `MinerURunner.run()` maps to `MinerURunError`.
Future cancellation improvements should preserve this behavior and append a clear
log trailer with the signal/timeout reason when possible.

## Failure contract

On failure, `parse-book` should surface the durable log path in the CLI error:

```text
Log: /path/to/workspace/runs/parse-book/<timestamp>-<book_id>.log
```

That lets humans and agents inspect the full run even when Typer truncates or
formats the exception body.

## Test guidance

Unit/CLI tests should use fake runner commands or injected `run_process` callables.
They should assert:

- the CLI prints a log path before/around runner execution;
- fake runner progress appears in the log;
- failure includes `Log: ...` in the error output;
- success and failure both append artifact summaries;
- `_mineru` work directories are still cleaned after staging/failure.
