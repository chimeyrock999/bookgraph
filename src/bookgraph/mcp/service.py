"""Pure reading/query logic behind the BookGraph MCP tools.

These functions operate on a :class:`WorkspacePaths` and the on-disk artifacts
written by the segment and reading-plan stages. They have no FastMCP dependency
so they can be unit-tested directly; the MCP server is a thin wrapper in
:mod:`bookgraph.mcp.server`.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from bookgraph.indexes import SectionIndex, index_path, read_index, tokenize
from bookgraph.models import ReadingPlan, Section
from bookgraph.reading_plans import mark_section_read, next_sections, read_reading_plan
from bookgraph.sections import read_sections
from bookgraph.utils import ID_PATTERN, validate_slug_id
from bookgraph.workspace import WorkspacePaths


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


class SectionView(BaseModel):
    """A section's full reading content plus provenance and its Markdown path."""

    id: str
    doc_id: str
    title: str
    level: int
    heading_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    text: str
    prev_id: str | None = None
    next_id: str | None = None
    block_ids: list[str] = Field(default_factory=list)
    markdown_path: str


class NextSection(BaseModel):
    """The next reading tick: the unread sections a reader should tackle next."""

    plan_id: str
    doc_id: str
    sections: list[SectionView]
    remaining: int
    done: bool


class MarkReadResult(BaseModel):
    """Outcome of marking a section read."""

    plan_id: str
    marked: str
    completed: int
    total: int
    done: bool


class SearchHit(BaseModel):
    """One section matched by :func:`search_sections`."""

    section_id: str
    doc_id: str
    title: str
    score: int
    snippet: str


class SearchResult(BaseModel):
    """Ranked search hits for a query."""

    query: str
    hits: list[SearchHit]


def _section_markdown_path(workspace: WorkspacePaths, doc_id: str, section_id: str) -> Path:
    return workspace.sources_sections / doc_id / f"{section_id}.md"


def _section_view(workspace: WorkspacePaths, section: Section) -> SectionView:
    markdown_path = _section_markdown_path(workspace, section.doc_id, section.id)
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


def _load_plan(workspace: WorkspacePaths, plan_id: str) -> tuple[Path, ReadingPlan]:
    _validate_id(plan_id, "plan_id")
    path = workspace.reading_plans_root / f"{plan_id}.json"
    if not path.is_file():
        raise PlanNotFoundError(
            f"Reading plan '{plan_id}' not found: {path}. "
            "Run 'bookgraph reading-plan create' first."
        )
    try:
        return path, read_reading_plan(path)
    except (OSError, ValueError) as exc:
        raise PlanNotFoundError(f"Invalid reading plan: {path}: {exc}") from exc


def get_next_section(workspace: WorkspacePaths, plan_id: str) -> NextSection:
    """Return the next unread sections for a plan, with full content."""

    _, plan = _load_plan(workspace, plan_id)
    pack = next_sections(plan)
    by_id = {section.id: section for section in _load_doc_sections(workspace, plan.doc_id)}
    views: list[SectionView] = []
    for section_id in pack.sections:
        section = by_id.get(section_id)
        if section is None:
            raise SectionNotFoundError(
                f"Reading plan '{plan_id}' references unknown section '{section_id}' "
                f"in document '{plan.doc_id}'."
            )
        views.append(_section_view(workspace, section))
    return NextSection(
        plan_id=plan.plan_id,
        doc_id=plan.doc_id,
        sections=views,
        remaining=pack.remaining,
        done=pack.done,
    )


def get_section(workspace: WorkspacePaths, doc_id: str, section_id: str) -> SectionView:
    """Return one section's full reading content by id."""

    for section in _load_doc_sections(workspace, doc_id):
        if section.id == section_id:
            return _section_view(workspace, section)
    raise SectionNotFoundError(f"Section '{section_id}' not found in document '{doc_id}'.")


def mark_read(
    workspace: WorkspacePaths, plan_id: str, section_id: str | None = None
) -> MarkReadResult:
    """Mark a section read for a plan and persist the updated plan."""

    path, plan = _load_plan(workspace, plan_id)
    try:
        updated, marked = mark_section_read(plan, section_id)
    except ValueError as exc:
        raise ReadingServiceError(str(exc)) from exc
    path.write_text(updated.model_dump_json(indent=2) + "\n")
    return MarkReadResult(
        plan_id=updated.plan_id,
        marked=marked,
        completed=len(updated.completed),
        total=len(updated.section_ids),
        done=len(updated.completed) == len(updated.section_ids),
    )


def _snippet(text: str, terms: list[str], width: int = 160) -> str:
    """A short excerpt around the first matching term, else the text head."""

    lowered = text.lower()
    positions = [pos for pos in (lowered.find(term) for term in terms) if pos >= 0]
    if not positions:
        excerpt = text[:width]
        return excerpt + ("…" if len(text) > width else "")
    start = max(0, min(positions) - width // 3)
    excerpt = text[start : start + width]
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + width < len(text) else ""
    return prefix + excerpt + suffix


def _hits_from_index(index: SectionIndex, terms: list[str]) -> list[SearchHit]:
    by_id = {section.id: section for section in index.sections}
    scores: dict[str, int] = {}
    for term in terms:
        for section_id, freq in index.postings.get(term, {}).items():
            scores[section_id] = scores.get(section_id, 0) + freq
    hits: list[SearchHit] = []
    for section_id, score in scores.items():
        indexed = by_id.get(section_id)
        if indexed is None or score <= 0:
            continue
        hits.append(
            SearchHit(
                section_id=section_id,
                doc_id=index.doc_id,
                title=indexed.title,
                score=score,
                snippet=_snippet(indexed.text, terms),
            )
        )
    return hits


def _hits_from_sections(sections: list[Section], terms: list[str]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for section in sections:
        counts = Counter(tokenize(f"{section.title}\n{section.text}"))
        score = sum(counts[term] for term in terms)
        if score > 0:
            hits.append(
                SearchHit(
                    section_id=section.id,
                    doc_id=section.doc_id,
                    title=section.title,
                    score=score,
                    snippet=_snippet(section.text, terms),
                )
            )
    return hits


def _hits_for_doc(workspace: WorkspacePaths, doc_id: str, terms: list[str]) -> list[SearchHit]:
    """Score one document, preferring its persisted index over a live scan."""

    path = index_path(workspace, doc_id)
    if path.is_file():
        try:
            return _hits_from_index(read_index(path), terms)
        except (OSError, ValueError):
            # A corrupt/stale index should not break search; fall back to the scan.
            pass
    return _hits_from_sections(_load_doc_sections(workspace, doc_id), terms)


def search_sections(
    workspace: WorkspacePaths,
    query: str,
    doc_id: str | None = None,
    limit: int = 10,
) -> SearchResult:
    """Rank sections by how often the query terms appear in title and text.

    Uses the persisted inverted index under ``indexes/sections/<doc_id>.json``
    when present (built by ``bookgraph index build``), and falls back to a live
    scan of ``sources/sections/<doc_id>/sections.jsonl`` for documents that have
    not been indexed yet. Pass ``doc_id`` to search a single document, or omit it
    to search every segmented document in the workspace.
    """

    terms = tokenize(query)
    if not terms:
        raise ReadingServiceError("search query must contain at least one term")
    if limit < 1:
        raise ReadingServiceError("limit must be at least 1")

    if doc_id is not None:
        doc_ids = [_validate_id(doc_id, "doc_id")]
    else:
        # Enumerated directory names are workspace-internal, not client input, but
        # only slug-shaped ones can have been produced by ``segment``; skipping the
        # rest keeps the per-doc id validation in ``_load_doc_sections`` from
        # aborting a workspace-wide search on a stray directory.
        root = workspace.sources_sections
        doc_ids = sorted(
            child.name
            for child in (root.iterdir() if root.is_dir() else [])
            if (child / "sections.jsonl").is_file() and ID_PATTERN.fullmatch(child.name)
        )

    hits: list[SearchHit] = []
    for current_doc in doc_ids:
        try:
            hits.extend(_hits_for_doc(workspace, current_doc, terms))
        except SectionsNotFoundError:
            if doc_id is not None:
                raise

    hits.sort(key=lambda hit: (-hit.score, hit.doc_id, hit.section_id))
    return SearchResult(query=query, hits=hits[:limit])
