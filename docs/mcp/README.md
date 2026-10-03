# BookGraph MCP docs

BookGraph MCP serves one prepared workspace over stdio so an AI agent can read long
books with you over time. The server supports the daily loop: fetch the next section,
inspect context/assets, discuss or answer questions, write summaries/concepts,
translate when needed, validate the batch, and persist progress for tomorrow.

The MCP server reads canonical BookGraph artifacts:

```text
sources/sections/      # section text + provenance + quality warnings
indexes/bookgraph.db   # search, graph, concepts
reading_plans/         # progress state
annotations/           # agent-authored summaries/concepts
translations/          # cached section translations
```

It does **not** read the generated wiki as its source of truth. The wiki and MCP tools
are parallel projections from the same sections and indexes.

Whole-book indexes give the agent a map; annotations and translations turn each day's
reading into durable memory and deliverable output.

## Guides

- [`reading-agent.md`](reading-agent.md) — prepare a workspace, start `bookgraph mcp`,
  configure an MCP client, and run the co-reading loop.
- [`llmwiki-integration.md`](llmwiki-integration.md) — optional llm-wiki-compiler MCP
  server alongside BookGraph for compiled-wiki search/query/context-pack workflows and
  local wiki browsing.

## Minimal server command

```bash
uv run --extra mcp bookgraph mcp /path/to/workspace
```

Tool arguments do not take a workspace path; the server is bound to the workspace used
at launch.
