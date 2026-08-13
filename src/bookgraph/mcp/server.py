"""FastMCP server exposing the BookGraph reading/query tools.

This module imports :mod:`fastmcp`, which ships only with the optional ``mcp``
extra, so it must be imported lazily (the CLI does this and reports a friendly
error when the extra is missing). All real logic lives in
:mod:`bookgraph.mcp.service`; the tools here are thin wrappers bound to one
workspace that translate service errors into MCP tool errors.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from bookgraph.mcp import service
from bookgraph.mcp.service import (
    MarkReadResult,
    NextSection,
    Outline,
    ReadingServiceError,
    RelatedSections,
    SearchResult,
    SectionContext,
    SectionView,
)
from bookgraph.workspace import WorkspacePaths


def build_server(workspace: WorkspacePaths) -> FastMCP:
    """Build a FastMCP server whose tools read/query a single workspace."""

    mcp: FastMCP = FastMCP("bookgraph")

    @mcp.tool
    def get_next_section(plan_id: str) -> NextSection:
        """Return the next unread sections for a reading plan, with full content."""

        try:
            return service.get_next_section(workspace, plan_id)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_section(doc_id: str, section_id: str) -> SectionView:
        """Return one section's full reading content by document and section id."""

        try:
            return service.get_section(workspace, doc_id, section_id)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def mark_read(plan_id: str, section_id: str | None = None) -> MarkReadResult:
        """Mark a section read for a plan (defaults to the next unread section)."""

        try:
            return service.mark_read(workspace, plan_id, section_id)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def search(query: str, doc_id: str | None = None, limit: int = 10) -> SearchResult:
        """Search segmented sections by term frequency in title and text."""

        try:
            return service.search_sections(workspace, query, doc_id, limit)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_outline(doc_id: str) -> Outline:
        """Return a document's section outline (heading hierarchy) in reading order."""

        try:
            return service.get_outline(workspace, doc_id)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_related(doc_id: str, section_id: str) -> RelatedSections:
        """Return a section's structural neighbours: parent, prev, next, and children."""

        try:
            return service.get_related(workspace, doc_id, section_id)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_context(doc_id: str, section_id: str) -> SectionContext:
        """Return a section's full content plus its graph neighbourhood."""

        try:
            return service.get_context(workspace, doc_id, section_id)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    return mcp


def create_server(workspace_path: Path) -> FastMCP:
    """Build a server bound to the workspace rooted at ``workspace_path``."""

    return build_server(WorkspacePaths(workspace_path.expanduser().resolve()))
