"""The derived index layer: a swappable search + section-graph store.

Everything index-related lives here — the :class:`IndexBackend` port, the default
SQLite/FTS5 backend, and their registry — so the storage engine can be replaced
by implementing the interface and registering a new backend, without touching the
service or CLI layers.
"""

from __future__ import annotations

from bookgraph.index.base import (
    IndexBackend,
    IndexSearchHit,
    IndexUnavailableError,
    tokenize,
)
from bookgraph.index.sqlite import SqliteIndexBackend
from bookgraph.plugins import PluginRegistry

DEFAULT_INDEX_BACKEND = "sqlite"


def default_index_backend_registry() -> PluginRegistry[IndexBackend]:
    """Registry of available index backends. Add a backend by registering it here."""

    registry: PluginRegistry[IndexBackend] = PluginRegistry(kind="index backend")
    registry.register(SqliteIndexBackend())
    return registry


def default_index_backend() -> IndexBackend:
    """The default index backend used by the CLI and MCP service."""

    return default_index_backend_registry().get(DEFAULT_INDEX_BACKEND)


__all__ = [
    "DEFAULT_INDEX_BACKEND",
    "IndexBackend",
    "IndexSearchHit",
    "IndexUnavailableError",
    "SqliteIndexBackend",
    "default_index_backend",
    "default_index_backend_registry",
    "tokenize",
]
