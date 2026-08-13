# BookGraph MCP docs

This directory contains user-facing setup and operating guides for connecting MCP
clients or reading agents to a BookGraph workspace.

Design notes and implementation contracts belong under `docs/design/`; CLI and
artifact contracts belong under `docs/cli/`.

Current guides:

- [`reading-agent.md`](reading-agent.md) — prepare a workspace, start `bookgraph
  mcp`, configure clients, and run the reading loop.
- [`llmwiki-integration.md`](llmwiki-integration.md) — run the optional `llmwiki`
  MCP server alongside BookGraph MCP for compiled-wiki search/query/context-pack
  workflows, and why BookGraph MCP stays the primary reading server.
