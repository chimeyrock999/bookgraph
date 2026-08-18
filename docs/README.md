# BookGraph documentation

BookGraph documentation is split by audience so users and implementation agents do
not have to infer which notes are operational guidance and which notes are design
contracts.

## User-facing docs

Use these when running BookGraph or wiring an agent to an existing workspace:

- [`installation.md`](installation.md) — install BookGraph and its optional extras
  (parsers, MinerU, MCP) with uv or pip.
- [`cli/`](cli/) — CLI commands, workspace layout, artifacts, and runtime guides.
- [`cli/parse-book-large-pdfs.md`](cli/parse-book-large-pdfs.md) — operational
  guide for long raw-PDF parses, MinerU logs, model caches, and diagnosis.
- [`mcp/reading-agent.md`](mcp/reading-agent.md) — how an MCP client reads with a
  BookGraph workspace.

## Design and implementation docs

Use these when changing internals, reviewing implementation contracts, or adding
new backends:

- [`design/`](design/) — design notes and runtime contracts for maintainers.
- [`design/parse-book-runtime.md`](design/parse-book-runtime.md) — parse-book
  subprocess/logging contract and test seams.

## Contract rule

CLI and artifact contracts live under `docs/cli/`; design rationale lives under
`docs/design/`. If behavior and docs disagree, update the relevant doc first and
then change code on a feature branch.
