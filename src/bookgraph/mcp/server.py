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
    AnnotationResult,
    ConceptInput,
    ConceptView,
    CreatedPlan,
    DocumentList,
    MarkReadResult,
    NextSection,
    Outline,
    PlanList,
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
        """Return a section's full content, graph neighbourhood, and its concepts."""

        try:
            return service.get_context(workspace, doc_id, section_id)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_concept(concept: str) -> ConceptView:
        """Return a concept and its cross-book backlink mentions across all books."""

        try:
            return service.get_concept(workspace, concept)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def annotate_section(
        doc_id: str,
        section_id: str,
        concepts: list[ConceptInput] | None = None,
        summary: str = "",
        model: str | None = None,
    ) -> AnnotationResult:
        """Write a Tier-2 annotation (real concepts + summary) for one section.

        Feeds a reading agent's judgment back into the concept graph. Writes only the
        annotation artifact; the summary shows immediately via get_context, while the
        concepts take effect on the next 'bookgraph index build <doc_id>'. Each concept
        is {title, slug?, gloss?}. The concepts argument has three intents: omit it
        (null) to leave the section's auto concepts untouched (e.g. a summary-only
        annotation); pass [] to prune the section's concepts; pass a list to replace
        them with the agent's authoritative set.
        """

        try:
            return service.annotate_section(
                workspace, doc_id, section_id, concepts, summary, model
            )
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def list_documents() -> DocumentList:
        """List the workspace's segmented documents (doc_id, title, section count)."""

        try:
            return service.list_documents(workspace)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def create_plan(
        doc_id: str,
        plan_id: str | None = None,
        daily_sections: int = 1,
        overwrite: bool = False,
    ) -> CreatedPlan:
        """Create a reading plan for a document (plan_id defaults to doc_id).

        Errors if a plan with that id already exists, to avoid discarding an
        in-progress plan on a re-call; pass overwrite=True to replace it.
        """

        try:
            return service.create_plan(
                workspace, doc_id, plan_id, daily_sections, overwrite=overwrite
            )
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def list_plans() -> PlanList:
        """List reading plans in the workspace with their completion progress."""

        try:
            return service.list_plans(workspace)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    return mcp


def create_server(workspace_path: Path) -> FastMCP:
    """Build a server bound to the workspace rooted at ``workspace_path``."""

    return build_server(WorkspacePaths(workspace_path.expanduser().resolve()))
