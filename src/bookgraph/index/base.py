"""Index backend port + shared search primitives.

The index layer (full-text search + the section graph) is **derived** from
``sources/sections/<doc_id>/sections.jsonl`` and persisted per workspace under
``workspace.indexes_root``. It is abstracted behind :class:`IndexBackend` so the
storage engine is swappable: a new engine (a different database, a file format,
a vector store) is a new class implementing this interface, registered in
:func:`bookgraph.index.default_index_backend_registry`. The default backend is
SQLite/FTS5 (:class:`bookgraph.index.sqlite.SqliteIndexBackend`).

Because the index is derived and fully rebuildable, it is never a source of
truth: a missing or corrupt index degrades to "not indexed" so callers fall back
to a live scan of the canonical section artifacts.
"""

from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod

from pydantic import BaseModel

from bookgraph.graph import SectionGraph
from bookgraph.models import Section
from bookgraph.workspace import WorkspacePaths

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Lowercase, fold diacritics, and split text into search tokens.

    Used to tokenize the query, and to score the live-scan fallback for documents
    that have not been indexed yet. Diacritics are stripped (NFKD, dropping
    combining marks) so tokens line up with what the default FTS5 backend stores
    (``unicode61 remove_diacritics 2``) — otherwise accented terms such as
    Vietnamese text would never match. Splitting keeps Unicode letters/digits, so
    non-ASCII words survive instead of being discarded.
    """

    normalized = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return _TOKEN_RE.findall(folded)


class IndexUnavailableError(RuntimeError):
    """The configured index backend cannot operate in this environment."""


class IndexSearchHit(BaseModel):
    """One section matched by a backend's search — engine-agnostic.

    ``score`` is normalised so that **higher is a better match**, regardless of the
    backend's native scoring direction, and carries ``text`` so the caller can
    build a snippet without re-reading the sections manifest.
    """

    doc_id: str
    section_id: str
    title: str
    text: str
    score: float


class IndexBackend(ABC):
    """Persist and query the derived search + graph index for a workspace.

    Implementations own their storage format under ``workspace.indexes_root``.
    Query methods must degrade gracefully when no usable index exists —
    ``indexed_doc_ids`` returns an empty set and ``load_graph`` returns ``None`` —
    rather than raising, so the service layer can fall back to a live scan.
    """

    name: str

    @abstractmethod
    def build_document(
        self, workspace: WorkspacePaths, doc_id: str, title: str, sections: list[Section]
    ) -> int:
        """Idempotently (re)build one document's index rows; return section count.

        Rebuilding a document must not affect any other document's rows. May raise
        :class:`IndexUnavailableError` if the engine is unusable here.
        """

    @abstractmethod
    def indexed_doc_ids(self, workspace: WorkspacePaths) -> set[str]:
        """The doc ids currently indexed (empty when no usable index exists)."""

    @abstractmethod
    def search(
        self,
        workspace: WorkspacePaths,
        terms: list[str],
        doc_ids: list[str] | None,
        limit: int,
    ) -> list[IndexSearchHit]:
        """Rank sections for ``terms``.

        ``doc_ids`` scopes the search to those documents; ``None`` searches every
        indexed document. Returns at most ``limit`` hits, best first.
        """

    @abstractmethod
    def load_graph(self, workspace: WorkspacePaths, doc_id: str) -> SectionGraph | None:
        """The document's section graph, or ``None`` when it is not indexed."""

    @abstractmethod
    def location(self, workspace: WorkspacePaths) -> str:
        """A human-readable description of where the index is persisted."""
