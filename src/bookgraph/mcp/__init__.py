"""BookGraph MCP serving.

The reading/query logic lives in :mod:`bookgraph.mcp.service` and has no
dependency on FastMCP, so it can be unit-tested without the optional ``mcp``
extra. :mod:`bookgraph.mcp.server` is the thin FastMCP wrapper and is imported
lazily (only when the ``mcp`` extra is installed).
"""

from __future__ import annotations

__all__ = ["service"]

from bookgraph.mcp import service
