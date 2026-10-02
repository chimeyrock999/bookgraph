"""Workspace loading and shared helpers for the MCP reading tools.

Sections, parsed blocks (cached), plans, and the section/chapter views built from
them. Used by every tool module behind :mod:`bookgraph.mcp.service`.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

from bookgraph.assets import asset_link, asset_reference, resolve_asset_path
from bookgraph.documents import read_document
from bookgraph.graph import SectionNode
from bookgraph.mcp.asset_views import AssetRef
from bookgraph.mcp.errors import (
    InvalidIdError,
    PlanNotFoundError,
    ReadingServiceError,
    SectionNotFoundError,
    SectionsNotFoundError,
)
from bookgraph.mcp.views import (
    ChapterProgressView,
    SectionRef,
    SectionView,
)
from bookgraph.models import (
    ASSET_BLOCK_TYPES,
    CanonicalBlock,
    ReadingPlan,
    Section,
)
from bookgraph.quality import (
    AssetSummary,
    classify_asset,
    section_warnings,
)
from bookgraph.reading_plans import (
    ChapterProgress,
    chapter_progress,
    next_sections,
    read_reading_plan,
)
from bookgraph.sections import read_sections
from bookgraph.utils import ID_PATTERN, validate_slug_id
from bookgraph.workspace import WorkspacePaths


def _section_markdown_path(workspace: WorkspacePaths, doc_id: str, section_id: str) -> Path:
    return workspace.sources_sections / doc_id / f"{section_id}.md"


# Parsed document.json blocks cached so an agent calling get_section in a loop does not
# re-read and re-validate the whole document on every call. Keyed by (mtime_ns, size): the
# nanosecond mtime plus byte size invalidates on any realistic re-parse (a same-tick rewrite
# to the exact same byte length is the one theoretical gap, only on coarse-mtime filesystems).
# Bounded by an LRU cap, and guarded by a lock so concurrent MCP requests keep the bound and
# recency order consistent.
_DocBlocks = dict[str, CanonicalBlock]
_DOC_BLOCKS_CACHE: OrderedDict[Path, tuple[int, int, _DocBlocks]] = OrderedDict()
_DOC_BLOCKS_CACHE_MAX = 32
_DOC_BLOCKS_LOCK = threading.Lock()


def _load_doc_blocks(workspace: WorkspacePaths, doc_id: str) -> _DocBlocks:
    """Index the parsed ``document.json`` blocks by id, or ``{}`` when unavailable.

    ``Section.block_ids`` is the only link back to the parser's richer blocks (where the
    image/table asset paths live). A document that was never parsed to ``document.json``
    (e.g. a fixture that only writes ``sections.jsonl``) simply yields no assets. Results
    are memoised by (mtime_ns, size) to keep per-section fetches O(1) across calls.
    """

    document_path = workspace.sources_parsed / doc_id / "document.json"
    try:
        stat = document_path.stat()
    except OSError:
        with _DOC_BLOCKS_LOCK:
            _DOC_BLOCKS_CACHE.pop(document_path, None)
        return {}
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _DOC_BLOCKS_LOCK:
        cached = _DOC_BLOCKS_CACHE.get(document_path)
        if cached is not None and (cached[0], cached[1]) == stamp:
            _DOC_BLOCKS_CACHE.move_to_end(document_path)
            return cached[2]
    # Read/validate outside the lock (the expensive part); a concurrent miss just re-reads.
    try:
        document = read_document(document_path)
    except (OSError, ValueError):
        return {}
    blocks = {block.id: block for block in document.blocks}
    with _DOC_BLOCKS_LOCK:
        _DOC_BLOCKS_CACHE[document_path] = (stamp[0], stamp[1], blocks)
        _DOC_BLOCKS_CACHE.move_to_end(document_path)
        while len(_DOC_BLOCKS_CACHE) > _DOC_BLOCKS_CACHE_MAX:
            _DOC_BLOCKS_CACHE.popitem(last=False)
    return blocks


def _section_assets(
    workspace: WorkspacePaths,
    section: Section,
    blocks_by_id: dict[str, CanonicalBlock],
) -> tuple[list[AssetRef], list[AssetSummary]]:
    """Resolve a section's asset blocks into openable ``AssetRef``s.

    Returns the assets a client can open plus a summary of **every** asset block of the
    section, including the ones whose file is missing — those are dropped from ``assets``
    (an ``AssetRef.path`` must always open) but still feed the quality checks, so a
    reference the parser never staged is reported rather than silently disappearing.
    """

    assets: list[AssetRef] = []
    summaries: list[AssetSummary] = []
    parsed_dir = workspace.sources_parsed / section.doc_id
    for block_id in section.block_ids:
        block = blocks_by_id.get(block_id)
        if block is None or block.type not in ASSET_BLOCK_TYPES:
            continue
        if not asset_reference(block):
            # An asset-typed block with no file reference at all (e.g. a markdown table
            # rendered inline into ``text``) is content, not a missing asset — skip it.
            continue
        path = resolve_asset_path(parsed_dir, block)
        summaries.append(
            AssetSummary(
                block_id=block.id,
                type=block.type,
                caption=block.text,
                resolved=path is not None,
                reference=asset_reference(block),
            )
        )
        if path is None:
            continue
        classification = classify_asset(block.type, block.text)
        assets.append(
            AssetRef(
                block_id=block.id,
                type=block.type,
                path=path,
                link=asset_link(parsed_dir, path),
                caption=block.text,
                order=block.order,
                page_idx=block.page_idx,
                type_confidence=classification.confidence,
                suggested_type=classification.suggested_type,
            )
        )
    return assets, summaries


def _section_view(
    workspace: WorkspacePaths,
    section: Section,
    *,
    include_assets: bool = True,
    blocks_by_id: dict[str, CanonicalBlock] | None = None,
) -> SectionView:
    markdown_path = _section_markdown_path(workspace, section.doc_id, section.id)
    assets: list[AssetRef] = []
    asset_summaries: list[AssetSummary] = []
    if include_assets:
        if blocks_by_id is None:
            blocks_by_id = _load_doc_blocks(workspace, section.doc_id)
        assets, asset_summaries = _section_assets(workspace, section, blocks_by_id)
    return SectionView(
        id=section.id,
        doc_id=section.doc_id,
        title=section.title,
        level=section.level,
        heading_path=section.heading_path,
        page_start=section.page_start,
        page_end=section.page_end,
        text=section.text,
        prev_id=section.prev_id,
        next_id=section.next_id,
        block_ids=section.block_ids,
        markdown_path=str(markdown_path),
        assets=assets,
        # The same checks back the segment stage's quality.json report, so a reader and
        # an ingest run never disagree about what is wrong with a section.
        warnings=section_warnings(section, asset_summaries),
    )


def _validate_id(value: str, field_name: str) -> str:
    """Reject client-supplied ids that are not filesystem-safe slugs.

    MCP tool inputs are client-controlled, so any id that becomes a path
    component (``plan_id``, ``doc_id``) must be validated before it is joined onto
    a workspace path — otherwise a value like ``../secret`` could escape the
    intended artifact directory.
    """

    try:
        return validate_slug_id(value, field_name=field_name)
    except ValueError as exc:
        raise InvalidIdError(str(exc)) from exc


def _load_doc_sections(workspace: WorkspacePaths, doc_id: str) -> list[Section]:
    _validate_id(doc_id, "doc_id")
    manifest = workspace.sources_sections / doc_id / "sections.jsonl"
    if not manifest.is_file():
        raise SectionsNotFoundError(
            f"No sections for '{doc_id}': {manifest} not found. Run 'bookgraph segment' first."
        )
    try:
        return read_sections(manifest)
    except (OSError, ValueError) as exc:
        raise SectionsNotFoundError(f"Invalid sections manifest: {manifest}: {exc}") from exc


def _plan_path(workspace: WorkspacePaths, plan_id: str) -> Path:
    return workspace.reading_plans_root / f"{_validate_id(plan_id, 'plan_id')}.json"


def _find_section(workspace: WorkspacePaths, doc_id: str, section_id: str) -> Section:
    """Look up a section by membership (rejects unknown and traversal ids)."""

    for section in _load_doc_sections(workspace, doc_id):
        if section.id == section_id:
            return section
    raise SectionNotFoundError(f"Section '{section_id}' not found in document '{doc_id}'.")


def _load_plan(workspace: WorkspacePaths, plan_id: str) -> tuple[Path, ReadingPlan]:
    path = _plan_path(workspace, plan_id)
    if not path.is_file():
        raise PlanNotFoundError(
            f"Reading plan '{plan_id}' not found: {path}. "
            "Run 'bookgraph reading-plan create' first."
        )
    try:
        return path, read_reading_plan(path)
    except (OSError, ValueError) as exc:
        raise PlanNotFoundError(f"Invalid reading plan: {path}: {exc}") from exc


def _chapter_progress(
    plan: ReadingPlan, sections: list[Section], chapter_level: int | None
) -> ChapterProgress:
    if chapter_level is not None and chapter_level < 1:
        raise ReadingServiceError("chapter_level must be at least 1")
    try:
        return chapter_progress(plan, sections, chapter_level=chapter_level)
    except ValueError as exc:  # the plan's next section is missing from the manifest
        raise SectionNotFoundError(str(exc)) from exc


def _chapter_view(
    progress: ChapterProgress, by_id: dict[str, Section]
) -> ChapterProgressView | None:
    if progress.chapter_id is None:
        return None
    boundary = (
        _section_ref(by_id[progress.next_boundary_id])
        if progress.next_boundary_id is not None
        else None
    )
    return ChapterProgressView(
        section=_section_ref(by_id[progress.chapter_id]),
        completed=progress.completed_in_chapter,
        remaining=progress.remaining_in_chapter,
        total=progress.total_in_chapter,
        next_boundary=boundary,
    )


def _section_ref(section: Section | SectionNode) -> SectionRef:
    return SectionRef(id=section.id, title=section.title, level=section.level)


def _current_batch(
    plan: ReadingPlan,
    sections: list[Section],
    *,
    stop_at_boundary: bool = False,
    chapter_level: int | None = None,
    progress: ChapterProgress | None = None,
) -> list[str]:
    """The plan's current batch: the one resolver behind ``get_next_section`` and the
    reading-batch tools, so the batch an agent was handed is the batch it completes.

    Without ``stop_at_boundary`` it is the next ``daily_sections`` unread sections; with
    it, that batch clipped at the end of the current chapter (``chapter_level`` picks
    the chapter's heading level). Pass an already computed ``progress`` to reuse it.
    """

    if not stop_at_boundary:
        return next_sections(plan).sections
    if progress is None:
        progress = _chapter_progress(plan, sections, chapter_level)
    return progress.next_section_ids


def _segmented_doc_ids(workspace: WorkspacePaths) -> list[str]:
    """Slug-shaped document directories that have a sections manifest.

    Enumerated directory names are workspace-internal, not client input, but only
    slug-shaped ones can have been produced by ``segment``; skipping the rest keeps
    the per-doc id validation in ``_load_doc_sections`` from aborting a
    workspace-wide search on a stray directory.
    """

    root = workspace.sources_sections
    return sorted(
        child.name
        for child in (root.iterdir() if root.is_dir() else [])
        if (child / "sections.jsonl").is_file() and ID_PATTERN.fullmatch(child.name)
    )
