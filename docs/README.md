# BookGraph documentation

BookGraph is a daily reading memory system for AI agents. It turns long books and
documents into source-grounded workspaces where an agent can read the next section
each day, enrich the book with summaries and verified concepts, answer questions from
accumulated memory, translate sections, and export translated or bilingual editions.

These docs are split by task so users, agents, and maintainers can find the right
contract without reading the whole repository.

## Start here

| Goal | Read |
| --- | --- |
| Install BookGraph or choose optional extras | [`installation.md`](installation.md) |
| Run the document pipeline from the CLI | [`cli/`](cli/) |
| Understand workspace files and JSON artifacts | [`cli/workspace.md`](cli/workspace.md), [`cli/artifacts.md`](cli/artifacts.md) |
| Parse large raw PDFs with MinerU 4 | [`cli/parse-book-large-pdfs.md`](cli/parse-book-large-pdfs.md) |
| Connect a co-reading AI agent over MCP | [`mcp/reading-agent.md`](mcp/reading-agent.md) |
| Run llm-wiki-compiler alongside BookGraph | [`mcp/llmwiki-integration.md`](mcp/llmwiki-integration.md) |
| Change internals or add adapters | [`design/`](design/) |

## User-facing docs

Use these when running BookGraph or preparing a workspace for an agent:

- [`installation.md`](installation.md) — Python requirements, release/source installs,
  optional extras, MinerU 4 notes, and verification commands.
- [`cli/`](cli/) — command groups, workspace layout, artifact schemas, index schema,
  concepts, annotations, exports, and operational guides.
- [`mcp/reading-agent.md`](mcp/reading-agent.md) — prepare a workspace, start
  `bookgraph mcp`, configure an MCP client, and run the daily read → annotate → recall
  → translate/export loop.
- [`mcp/llmwiki-integration.md`](mcp/llmwiki-integration.md) — optional compiled-wiki
  query/view workflows; BookGraph MCP remains the primary reading server.

## Maintainer docs

Use these when changing behavior or reviewing implementation contracts:

- [`design/`](design/) — design notes, invariants, and runtime contracts.
- [`design/parse-book-runtime.md`](design/parse-book-runtime.md) — `parse-book`
  subprocess/logging contract and test seams.

## Contract rule

CLI and artifact contracts live under `docs/cli/`; design rationale lives under
`docs/design/`. If behavior and docs disagree, update the relevant doc first and then
change code on a feature branch.
