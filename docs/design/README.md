# BookGraph design docs

This directory is for maintainers and implementation agents. Put internal
contracts, design rationale, invariants, runtime behavior, and test seams here.

Do **not** put user-facing runbooks here. Operational instructions such as how to
run a command, diagnose a workspace, or connect an MCP client belong under
`docs/cli/` or `docs/mcp/`.

Current notes:

- [`parse-book-runtime.md`](parse-book-runtime.md) — streaming subprocess/logging
  contract for long raw-PDF parser runs.
