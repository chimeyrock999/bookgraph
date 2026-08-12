# BookGraph CLI Contracts

This directory is the source of truth for CLI-facing contracts. CLI work lands here first so other agents can implement parser, segmenter, wiki, and MCP features against stable file/layout expectations.

## Rules for agents

- Treat files in `.docs/cli/` as contracts, not casual notes.
- If implementation and contract disagree, stop and update the contract first on `main`, then implement on a feature branch.
- If a command fails because a dependency/behavior is missing, read these docs before changing code.
- CLI commands must write deterministic filesystem artifacts under one explicit workspace/output root.
- Do not make parser/segmenter/wiki stages run implicitly unless the contract says so.
- Preserve provenance fields whenever transforming source content.

## Contract files

- `workspace.md` — canonical workspace paths and naming rules.
- `commands.md` — CLI command contracts, inputs, outputs, side effects, and error behavior.
- `artifacts.md` — JSON/Markdown artifact schemas and status transitions.
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
