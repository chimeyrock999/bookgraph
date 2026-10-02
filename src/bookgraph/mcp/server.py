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

from bookgraph.mcp import reading_batch, service
from bookgraph.mcp.reading_batch import BatchRequirements, IndexPolicy, ReadingBatchReport
from bookgraph.mcp.service import (
    AnnotationResult,
    ChapterOutline,
    ConceptHygieneReport,
    ConceptInput,
    ConceptView,
    CreatedPlan,
    DocumentList,
    MarkReadResult,
    NextSection,
    Outline,
    PlanList,
    PlanProgress,
    ReadingServiceError,
    RelatedSections,
    SearchResult,
    SectionArtifactList,
    SectionArtifactView,
    SectionContext,
    SectionTree,
    SectionView,
)
from bookgraph.models import TranslationUnit
from bookgraph.workspace import WorkspacePaths

# Sent to every client at connect time, before any tool is called: the rules a reading
# or translation agent must follow so its artifacts stay reusable book content.
SERVER_INSTRUCTIONS = """\
BookGraph serves books section by section. Artifacts you write (translations, annotation
summaries and glosses) are reused by later runs and printed in reading PDFs, so they
hold book content only. Keep the channels separate:
- translated headings/prose/tables/figures -> write_section_translation(units=...), one
  {source_block_ids, content} unit per paragraph with the ids from
  get_section(include_blocks=True), so the bilingual export interleaves paragraph by
  paragraph (plain content=... still works, paired per section);
  link each figure/table by its AssetRef.link (relative), never by its absolute path;
  translate prose and link labels, but keep link destinations, fragment ids, file
  paths, reference identifiers, HTML anchors and {#id} heading ids byte-for-byte
  (structure_issues in the write result lists any that changed);
- QA/checker results, terminology decisions, doubts about the source ->
  write_section_translation(notes=...), stored beside the translation, never exported;
- MEDIA:/path delivery markers, "saved cache / marked read" progress lines, job status
  -> your final chat reply only;
- export coverage/freshness/missing-asset status -> the export's .report.json; never
  copy labels such as (original), (untracked) or "Missing asset:" into a translation.
The translation registry is the only translation store: check get_section_translation
first, and save a translation ONLY with write_section_translation. Do not write
translation files yourself, under translations/ or in any directory of your own:
nothing reads them, so the section stays untranslated in the export and in batch
completion.
In a job that translates or annotates, do not call mark_read. Finish each batch with
complete_reading_batch, and in a translation job always pass translation_lang: without
it the batch completes without checking that anything was saved. A translation-only
job (no annotating, no index build) also passes require_annotation=False and
index="ignore" (or "deferred"), and lists the block ids of the figures it opened in
inspected_assets; the defaults require an annotation and a fresh index. If committed
is false, fix every blocking issue and call it again.
"""


def build_server(workspace: WorkspacePaths) -> FastMCP:
    """Build a FastMCP server whose tools read/query a single workspace."""

    mcp: FastMCP = FastMCP("bookgraph", instructions=SERVER_INSTRUCTIONS)

    @mcp.tool
    def get_next_section(
        plan_id: str,
        include_assets: bool = True,
        stop_at_boundary: bool = False,
        chapter_level: int | None = None,
    ) -> NextSection:
        """Return the next unread sections for a reading plan, with full content.

        With ``include_assets`` (default) each section carries its structured figure/table
        ``assets``; set it false to skip asset resolution when reading for prose only.
        Each section also carries its data-quality ``warnings`` (see ``get_section``).

        ``chapter`` reports progress within the chapter holding the next unread section
        (as ``get_plan_progress``). Pass ``stop_at_boundary=True`` to clip the batch at
        the end of that chapter; ``chapter_level`` picks the chapter's heading level
        (default: the top-level ancestor, skipping a lone book-title root; pass e.g. 2
        when chapters sit under level-1 parts).
        """

        try:
            return service.get_next_section(
                workspace,
                plan_id,
                include_assets,
                stop_at_boundary=stop_at_boundary,
                chapter_level=chapter_level,
            )
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_plan_progress(plan_id: str, chapter_level: int | None = None) -> PlanProgress:
        """Report a plan's progress overall and within its current chapter.

        Answers "how many sections are left in this chapter?" without fetching section
        text or the full outline: the current chapter (heading of the next unread
        section's scope), ``completed``/``remaining``/``total`` within it, the
        ``next_boundary`` section that starts after it, and ``next_sections`` — the next
        ``daily_sections`` batch clipped at that boundary. Counts are by membership, so
        reset plans and skipped or out-of-order reads stay correct. ``chapter_level``
        picks the chapter's heading level (default: the top-level ancestor, skipping a
        lone book-title root; pass e.g. 2 when chapters sit under level-1 parts).
        """

        try:
            return service.get_plan_progress(workspace, plan_id, chapter_level)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_section(
        doc_id: str, section_id: str, include_assets: bool = True, include_blocks: bool = False
    ) -> SectionView:
        """Return one section's full reading content by document and section id.

        With ``include_assets`` (default) the result carries a structured ``assets`` list
        of the section's figures/tables (path, type, caption, order) — each scored with a
        ``type_confidence`` and, when the caption disputes the parser's type, a
        ``suggested_type``; set it false to omit.

        ``warnings`` reports the section's data-quality anomalies (a broken page span, a
        disputed asset type, text that is only asset captions), so a reader does not have
        to inspect the parsed ``document.json`` to notice them. Page-range warnings are
        always present; asset warnings need ``include_assets``.

        ``include_blocks`` adds ``blocks``: the section's parsed source blocks (id, type,
        text) in reading order — the ids a block-aligned translation's units reference.
        """

        try:
            return service.get_section(
                workspace, doc_id, section_id, include_assets, include_blocks
            )
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def mark_read(plan_id: str, section_id: str | None = None) -> MarkReadResult:
        """Mark a section read for a plan (defaults to the next unread section).

        It checks nothing. In a job that translates or annotates, finish each batch
        with complete_reading_batch (passing translation_lang) instead, so progress
        only advances once the work is saved.
        """

        try:
            return service.mark_read(workspace, plan_id, section_id)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def validate_reading_batch(
        plan_id: str,
        section_ids: list[str] | None = None,
        require_annotation: bool = True,
        index: IndexPolicy = "fresh",
        require_assets: bool = True,
        inspected_assets: list[str] | None = None,
        translation_lang: str | None = None,
        artifacts: list[str] | None = None,
        stop_at_boundary: bool = False,
        chapter_level: int | None = None,
    ) -> ReadingBatchReport:
        """Check whether a reading batch is ready to be marked read, without writing.

        Same checks and arguments as complete_reading_batch; returns every problem as
        an issue {code, message, section_id, blocking}. section_ids defaults to the
        plan's current batch, resolved like get_next_section for the same
        stop_at_boundary/chapter_level.
        """

        requirements = BatchRequirements(
            require_annotation=require_annotation,
            index=index,
            require_assets=require_assets,
            inspected_assets=inspected_assets or [],
            translation_lang=translation_lang,
            artifacts=artifacts or [],
        )
        try:
            return reading_batch.validate_reading_batch(
                workspace,
                plan_id,
                section_ids,
                requirements,
                stop_at_boundary=stop_at_boundary,
                chapter_level=chapter_level,
            )
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def complete_reading_batch(
        plan_id: str,
        section_ids: list[str] | None = None,
        require_annotation: bool = True,
        index: IndexPolicy = "fresh",
        require_assets: bool = True,
        inspected_assets: list[str] | None = None,
        translation_lang: str | None = None,
        artifacts: list[str] | None = None,
        stop_at_boundary: bool = False,
        chapter_level: int | None = None,
    ) -> ReadingBatchReport:
        """Mark a whole reading batch read — only if its enrichment is complete.

        Use instead of mark_read when a batch involves annotation/translation/index
        work: progress advances for every section at once or not at all. Checks, per
        section: an annotation exists (require_annotation); the index reflects it
        (index="fresh" blocks, "deferred" only reports, "ignore" skips); every
        figure/table file was inspected — list their block ids in inspected_assets
        (require_assets); the cached translation for translation_lang exists and is not
        stale in the translation registry; and each artifacts path template exists ({doc_id},
        {section_id}, {plan_id} expand). If any blocking issue is found, nothing is
        written: committed=false and issues lists every reason to fix before retrying.
        section_ids defaults to the plan's current batch, resolved exactly like
        get_next_section: if you read with stop_at_boundary=True (and a chapter_level),
        pass the same values here so sections past the chapter boundary are not marked.
        """

        requirements = BatchRequirements(
            require_annotation=require_annotation,
            index=index,
            require_assets=require_assets,
            inspected_assets=inspected_assets or [],
            translation_lang=translation_lang,
            artifacts=artifacts or [],
        )
        try:
            return reading_batch.complete_reading_batch(
                workspace,
                plan_id,
                section_ids,
                requirements,
                stop_at_boundary=stop_at_boundary,
                chapter_level=chapter_level,
            )
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
    def get_outline(
        doc_id: str, root_id: str | None = None, max_depth: int | None = None
    ) -> Outline:
        """Return a document's section outline (heading hierarchy) in reading order.

        Full-book outlines can be very large; prefer a scoped call. ``root_id`` limits
        the outline to that section's subtree; ``max_depth`` keeps that many tree levels
        (1 = top-level sections only, or ``root_id`` alone; on a flat, all-top-level
        document that is still every section, so check ``total_nodes``). ``truncated``
        says whether deeper sections were cut off — drill in with ``root_id`` from a
        node's ``child_ids``. For "where am I" questions, see ``get_section_tree`` and
        ``get_chapter_outline``.
        """

        try:
            return service.get_outline(workspace, doc_id, root_id, max_depth)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_section_tree(
        doc_id: str,
        section_id: str,
        include_siblings: bool = True,
        include_children: bool = True,
        sibling_window: int | None = 10,
    ) -> SectionTree:
        """Return a small outline around one section: breadcrumb, siblings, children.

        ``ancestors`` run root-first; ``siblings`` (the section itself included) are in
        reading order, at most ``sibling_window`` on each side (null = all;
        ``siblings_truncated`` says whether any were dropped). A cheap alternative to the
        full ``get_outline``.
        """

        try:
            return service.get_section_tree(
                workspace, doc_id, section_id, include_siblings, include_children,
                sibling_window,
            )
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_chapter_outline(
        plan_id: str, max_depth: int | None = 2, chapter_level: int | None = None
    ) -> ChapterOutline:
        """Return the outline of the chapter a reading plan is currently in.

        The chapter is the outermost ancestor of the plan's next unread section (a lone
        book-title root is skipped), or with ``chapter_level`` the nearest one at that
        heading level or above (use 2 when chapters sit under parts). It always matches
        ``get_plan_progress``. While such a wrapper heading (book root or part) is itself
        the next unread section, it comes back alone (``total == 1``) — that is
        expected, not a failed lookup. Each node carries a ``read``
        flag; ``completed``/``remaining``/``total`` count the chapter, and
        ``plan_completed``/``plan_total`` the whole plan. ``max_depth`` (default 2: the
        chapter and its direct subsections; null = full subtree) limits the nodes as in
        ``get_outline``.
        """

        try:
            return service.get_chapter_outline(workspace, plan_id, max_depth, chapter_level)
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
    def get_context(
        doc_id: str, section_id: str, include_assets: bool = True
    ) -> SectionContext:
        """Return a section's full content, graph neighbourhood, and its concepts.

        ``include_assets`` (default true) controls whether the embedded section carries its
        structured figure/table ``assets`` and their quality ``warnings`` (see
        ``get_section``).
        """

        try:
            return service.get_context(workspace, doc_id, section_id, include_assets)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_concept(concept: str, include_annotations: bool = False) -> ConceptView:
        """Return a concept and its cross-book backlink mentions across all books.

        An alias slug resolves to its canonical concept: the result carries the
        canonical slug/title, its aliases, and resolved_from (the requested alias).

        Defaults to a compact card (bare backlinks with per-section glosses) for
        lightweight graph traversal. Pass include_annotations=True for the detail
        view, where each mention also carries its section's Tier-2 summary, so a
        concept with several mentions reads as a source-grounded note.
        """

        try:
            return service.get_concept(workspace, concept, include_annotations)
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def concept_hygiene(limit: int = 20, threshold: float = 0.5) -> ConceptHygieneReport:
        """Report concept-graph hygiene: likely duplicates, lint, and a review queue.

        merge_suggestions pairs concepts that look like duplicates (inflections,
        acronyms, one subsuming the other, near-identical spelling) with a suggested
        canonical side; lint flags generic, one-off, over-granular, or stale concepts;
        review_queue lists agent-created concepts not yet reviewed. Read-only — a human
        applies decisions with the 'bookgraph concepts' CLI and then rebuilds the index.
        """

        try:
            return service.concept_hygiene(workspace, limit, threshold)
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
        them with the agent's authoritative set. Summary and glosses are book
        explanation only — keep progress, QA results and MEDIA: markers in your chat
        reply.
        """

        try:
            return service.annotate_section(
                workspace, doc_id, section_id, concepts, summary, model
            )
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def get_section_translation(
        doc_id: str, section_id: str, lang: str, include_content: bool = True
    ) -> SectionArtifactView:
        """Return a section's cached translation (e.g. lang='vi') and its freshness.

        Check this before translating a section. ``status`` is 'fresh' (reuse
        ``content`` as-is), 'stale' (the section changed since — retranslate),
        'untracked' (a cached file with no registry record — freshness unknown), or
        'missing' (translate it). Reuse only when status is 'fresh' AND
        (includes_assets or not section_has_assets): a fresh prose-only translation of
        a section with figures/tables is incomplete. includes_assets is verified
        against the body; missing_assets lists the figures/tables it does not link,
        each with the link to add. Pass
        ``current_section_hash`` back to write_section_translation to pin your write.
        ``alignment_status`` is 'aligned' (written with units; alignment_issues lists
        source text blocks no unit translates and units that merged into the previous
        one), 'unaligned' (plain content — valid, the
        bilingual export pairs it per section), or 'invalid' (the stored alignment no
        longer fits the section; rewrite it with units).
        """

        try:
            return service.get_section_translation(
                workspace, doc_id, section_id, lang, include_content
            )
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def write_section_translation(
        doc_id: str,
        section_id: str,
        lang: str,
        content: str = "",
        includes_assets: bool = False,
        model: str | None = None,
        source_section_hash: str | None = None,
        notes: str | None = None,
        units: list[TranslationUnit] | None = None,
    ) -> SectionArtifactView:
        """Cache a section translation (Markdown) and register it as fresh.

        Writes translations/<lang>/<doc_id>/<section_id>.md plus a registry record of
        the section content it was made from, replacing any earlier translation. Set
        includes_assets=True when the translation carries the section's figures/tables:
        it is checked against the body (each staged figure/table must be linked by its
        AssetRef.link), and a claim the body does not back is refused with the missing
        links. Pass source_section_hash (the current_section_hash you saw) to refuse the
        write if the section changed while you were translating. content is the
        translated book content only: link each figure/table by its AssetRef.link
        (relative), never its absolute path. Put QA/checker results, terminology
        decisions and any other remarks in notes (stored beside the translation, never
        in it); keep MEDIA: markers and progress lines for your final chat reply. This
        is the only translation store: never write translation files yourself, not under
        translations/ and not in any directory of your own (such as a
        translation_cache/) — nothing reads them, so the section stays untranslated in
        the export and in batch completion.

        Translate prose and link labels, not structure: keep link destinations and
        fragment ids, image/file paths, reference-style identifiers, HTML id/name
        anchors, and {#id} heading ids byte-for-byte. structure_issues in the result
        lists any that changed; fix and rewrite the translation.

        Prefer units over content: a list of {source_block_ids, content}, one per
        translated paragraph, in reading order, with the ids from
        get_section(include_blocks=True). Merge paragraphs by listing several ids in one
        unit; split one by repeating its id in consecutive units. Headings, figures,
        tables and code may share a unit with their prose or be left out. The units are
        joined into the Markdown body, and the alignment lets the bilingual export set
        each paragraph beside its translation. A unit without content or ids, an id
        outside the section, or units out of source order is refused; a source text
        block no unit translates, or a unit that continues the previous unit's list or
        fence instead of starting its own Markdown block, is reported in
        alignment_issues.
        """

        try:
            return service.write_section_translation(
                workspace,
                doc_id,
                section_id,
                lang,
                content,
                includes_assets,
                model,
                source_section_hash,
                notes,
                units,
            )
        except ReadingServiceError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def list_section_artifacts(
        doc_id: str | None = None, lang: str | None = None, type: str = "translation"
    ) -> SectionArtifactList:
        """List cached section translations with their freshness (no bodies).

        Filter by doc_id and/or lang. Use it to find 'stale' translations to redo, or
        'orphaned' ones whose section no longer exists after re-segmenting.
        """

        try:
            return service.list_section_artifacts(workspace, doc_id, lang, type)
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
