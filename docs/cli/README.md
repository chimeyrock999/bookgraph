# BookGraph CLI documentation

This directory contains user-facing CLI docs and the CLI/artifact contracts that
BookGraph agents implement against. Design rationale and internal runtime notes
belong under `docs/design/` instead.

## Rules for agents

- Treat contract files in `docs/cli/` as stable behavior, not casual notes.
- Put operational "how do I run/diagnose this?" guidance in `docs/cli/`.
- Put implementation rationale, invariants, and test seams in `docs/design/`.
- If implementation and contract disagree, stop and update the contract first on `main`, then implement on a feature branch.
- If a command fails because a dependency/behavior is missing, read these docs before changing code.
- CLI commands must write deterministic filesystem artifacts under one explicit workspace/output root.
- Do not make parser/segmenter/wiki stages run implicitly unless the contract says so.
- Preserve provenance fields whenever transforming source content.

## Contract files

- `workspace.md` — canonical workspace paths and naming rules.
- `commands.md` — CLI command contracts, inputs, outputs, side effects, and error behavior.
- `artifacts.md` — JSON/Markdown artifact schemas and status transitions.
- `index.md` — the `indexes/bookgraph.db` SQLite schema, build, and query contract.
- `parse-book-large-pdfs.md` — user-facing runtime/diagnosis guide for long raw-PDF parses.
- `handoff.md` — feature-branch and cross-agent integration workflow.

## Current command groups

Implemented:

- `bookgraph init`
- `bookgraph paths`
- `bookgraph add-book`
- `bookgraph parsers`
- `bookgraph parse`

Planned:

- parser runner commands for raw PDF/MinerU invocation
- segmentation commands
- wiki compile commands
- reading-plan commands
- MCP/server commands
