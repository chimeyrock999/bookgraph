"""Errors raised by the MCP reading service (:mod:`bookgraph.mcp.service`)."""

from __future__ import annotations


class ReadingServiceError(Exception):
    """Base class for expected, client-facing reading-service failures."""


class InvalidIdError(ReadingServiceError):
    """A client-supplied id is not a filesystem-safe slug."""


class PlanNotFoundError(ReadingServiceError):
    """A requested reading plan does not exist."""


class SectionsNotFoundError(ReadingServiceError):
    """A document has no sections manifest (it has not been segmented)."""


class SectionNotFoundError(ReadingServiceError):
    """A requested section id does not exist in a document."""


class ConceptNotFoundError(ReadingServiceError):
    """A requested concept slug is not present in the index."""
