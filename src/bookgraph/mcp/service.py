"""Pure reading/query logic behind the BookGraph MCP tools (public facade).

These functions operate on a :class:`WorkspacePaths` and the on-disk artifacts
written by the segment and reading-plan stages. They have no FastMCP dependency
so they can be unit-tested directly; the MCP server is a thin wrapper in
:mod:`bookgraph.mcp.server`.

The implementation is split by tool family (``errors``, ``views``, ``loading``,
``reading_tools``, ``query_tools``, ``concept_tools``, ``translation_tools``); this
module re-exports their public API so callers keep importing from one place.
"""

from __future__ import annotations

from bookgraph.mcp.asset_views import AssetRef
from bookgraph.mcp.concept_tools import (
    annotate_section,
    concept_hygiene,
    get_concept,
)
from bookgraph.mcp.errors import (
    ConceptNotFoundError,
    InvalidIdError,
    PlanNotFoundError,
    ReadingServiceError,
    SectionNotFoundError,
    SectionsNotFoundError,
)
from bookgraph.mcp.query_tools import (
    get_chapter_outline,
    get_context,
    get_outline,
    get_related,
    get_section_tree,
    search_sections,
)
from bookgraph.mcp.reading_tools import (
    create_plan,
    get_next_section,
    get_plan_progress,
    get_section,
    list_documents,
    list_plans,
    mark_read,
)
from bookgraph.mcp.translation_tools import (
    get_section_translation,
    list_section_artifacts,
    write_section_translation,
)
from bookgraph.mcp.views import (
    AnnotationResult,
    ChapterOutline,
    ChapterProgressView,
    ConceptHygieneReport,
    ConceptInput,
    ConceptMentionView,
    ConceptRef,
    ConceptView,
    CreatedPlan,
    DocumentList,
    DocumentRef,
    MarkReadResult,
    NextSection,
    Outline,
    OutlineNode,
    PlanList,
    PlanProgress,
    PlanRef,
    ProgressNode,
    RelatedSections,
    SearchHit,
    SearchResult,
    SectionArtifactList,
    SectionArtifactView,
    SectionContext,
    SectionRef,
    SectionTree,
    SectionView,
)

__all__ = [
    "AnnotationResult",
    "AssetRef",
    "ChapterOutline",
    "ChapterProgressView",
    "ConceptHygieneReport",
    "ConceptInput",
    "ConceptMentionView",
    "ConceptNotFoundError",
    "ConceptRef",
    "ConceptView",
    "CreatedPlan",
    "DocumentList",
    "DocumentRef",
    "InvalidIdError",
    "MarkReadResult",
    "NextSection",
    "Outline",
    "OutlineNode",
    "PlanList",
    "PlanNotFoundError",
    "PlanProgress",
    "PlanRef",
    "ProgressNode",
    "ReadingServiceError",
    "RelatedSections",
    "SearchHit",
    "SearchResult",
    "SectionArtifactList",
    "SectionArtifactView",
    "SectionContext",
    "SectionNotFoundError",
    "SectionRef",
    "SectionTree",
    "SectionView",
    "SectionsNotFoundError",
    "annotate_section",
    "concept_hygiene",
    "create_plan",
    "get_chapter_outline",
    "get_concept",
    "get_context",
    "get_next_section",
    "get_outline",
    "get_plan_progress",
    "get_related",
    "get_section",
    "get_section_translation",
    "get_section_tree",
    "list_documents",
    "list_plans",
    "list_section_artifacts",
    "mark_read",
    "search_sections",
    "write_section_translation",
]
