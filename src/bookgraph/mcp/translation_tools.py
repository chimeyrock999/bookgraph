"""Section artifact registry (translation) tools (re-exported by
:mod:`bookgraph.mcp.service`)."""

from __future__ import annotations

from datetime import UTC, datetime

from bookgraph.mcp.errors import (
    InvalidIdError,
    ReadingServiceError,
)
from bookgraph.mcp.loading import (
    _find_section,
    _load_doc_blocks,
    _load_doc_sections,
    _section_assets,
    _validate_id,
)
from bookgraph.mcp.translation_structure import translation_structure_issues
from bookgraph.mcp.views import (
    SectionArtifactList,
    SectionArtifactView,
)
from bookgraph.models import (
    Section,
)
from bookgraph.translations import (
    TranslationState,
    iter_translation_keys,
    section_content_hash,
    translation_state,
    validate_lang,
    write_translation,
)
from bookgraph.workspace import WorkspacePaths

# --- Section artifact registry (translations) ------------------------------------
#
# A reading job that translates sections caches each result under
# ``translations/<lang>/<doc_id>/<section_id>.md`` with a registry sidecar recording the
# section content hash it was made from, so a workflow can tell a reusable translation
# from a stale one without re-deriving it from path conventions. Pure storage logic
# lives in :mod:`bookgraph.translations`.


def _section_has_assets(workspace: WorkspacePaths, section: Section) -> bool:
    """Whether the section owns any figure/table asset block (staged or not)."""

    _, summaries = _section_assets(workspace, section, _load_doc_blocks(workspace, section.doc_id))
    return bool(summaries)


def _artifact_view(
    workspace: WorkspacePaths,
    state: TranslationState,
    section: Section | None,
    *,
    include_content: bool,
) -> SectionArtifactView:
    artifact = state.artifact
    body_exists = state.status != "missing"
    content: str | None = None
    if include_content and state.body is not None:
        # Decode the bytes the status was computed from (no second read of the file).
        content = state.body.decode("utf-8", errors="replace")
    return SectionArtifactView(
        lang=state.lang,
        doc_id=state.doc_id,
        section_id=state.section_id,
        status=state.status,
        path=str(state.paths.body) if body_exists else None,
        metadata_path=str(state.paths.metadata) if artifact is not None else None,
        source_section_hash=artifact.source_section_hash if artifact else None,
        current_section_hash=state.current_section_hash,
        includes_assets=artifact.includes_assets if artifact else None,
        section_has_assets=_section_has_assets(workspace, section) if section else False,
        model=artifact.model if artifact else None,
        created_at=artifact.created_at if artifact else None,
        notes=artifact.notes if artifact else None,
        content=content,
        structure_issues=translation_structure_issues(
            workspace, state, section, _load_doc_blocks(workspace, state.doc_id)
        ),
    )


def _validate_lang(lang: str) -> str:
    try:
        return validate_lang(lang)
    except ValueError as exc:
        raise InvalidIdError(str(exc)) from exc


def get_section_translation(
    workspace: WorkspacePaths,
    doc_id: str,
    section_id: str,
    lang: str,
    include_content: bool = True,
) -> SectionArtifactView:
    """Return a section's cached translation and whether it is still fresh.

    Never raises for a missing translation — ``status="missing"`` is the normal "go
    translate it" answer, and it still carries ``current_section_hash`` so the caller
    can pin its later write.
    """

    resolved_doc_id = _validate_id(doc_id, "doc_id")
    resolved_lang = _validate_lang(lang)
    section = _find_section(workspace, resolved_doc_id, section_id)
    state = translation_state(
        workspace, resolved_lang, resolved_doc_id, section.id, section_content_hash(section)
    )
    return _artifact_view(workspace, state, section, include_content=include_content)


def write_section_translation(
    workspace: WorkspacePaths,
    doc_id: str,
    section_id: str,
    lang: str,
    content: str,
    includes_assets: bool = False,
    model: str | None = None,
    source_section_hash: str | None = None,
    notes: str | None = None,
) -> SectionArtifactView:
    """Cache a section translation and register it against the section's content.

    ``source_section_hash`` (optional) is the ``current_section_hash`` the caller saw
    when it fetched the section to translate; if the section has changed since, the
    write is refused so a translation of old content is never registered as fresh.
    ``includes_assets`` declares whether the section's figures/tables were carried into
    the translation.

    This is the only translation store: a translation file written anywhere else (under
    ``translations/`` by hand, or an agent's own ``translation_cache/``) is never read.
    ``content`` is the translated book content only, with figures linked by their
    ``AssetRef.link``. Everything else the job wants to record about the translation —
    QA/checker results, terminology decisions — goes in ``notes``: stored in the
    registry sidecar, returned by ``get_section_translation``, never part of the body
    or the export.
    """

    resolved_doc_id = _validate_id(doc_id, "doc_id")
    resolved_lang = _validate_lang(lang)
    if not content.strip():
        raise ReadingServiceError("translation content must not be empty")
    section = _find_section(workspace, resolved_doc_id, section_id)
    current_hash = section_content_hash(section)
    if source_section_hash is not None and source_section_hash != current_hash:
        raise ReadingServiceError(
            f"section '{section.id}' changed since it was translated "
            f"(translated {source_section_hash}, current {current_hash}); "
            "re-fetch it with get_section and translate the current content"
        )
    write_translation(
        workspace,
        section,
        resolved_lang,
        content,
        includes_assets=includes_assets,
        model=model,
        created_at=datetime.now(UTC).isoformat(),
        notes=notes,
    )
    state = translation_state(workspace, resolved_lang, resolved_doc_id, section.id, current_hash)
    return _artifact_view(workspace, state, section, include_content=False)


def list_section_artifacts(
    workspace: WorkspacePaths,
    doc_id: str | None = None,
    lang: str | None = None,
    artifact_type: str = "translation",
) -> SectionArtifactList:
    """List cached section artifacts with their freshness (no bodies).

    Each entry is checked against the document's current sections, so a reading job can
    find stale translations to redo — and ``orphaned`` ones whose section is gone —
    in one call. Only ``artifact_type="translation"`` exists today.
    """

    if artifact_type != "translation":
        raise ReadingServiceError(
            f"unknown artifact type {artifact_type!r}; supported: 'translation'"
        )
    resolved_doc_id = _validate_id(doc_id, "doc_id") if doc_id is not None else None
    resolved_lang = _validate_lang(lang) if lang is not None else None

    sections_by_doc: dict[str, dict[str, Section]] = {}
    artifacts: list[SectionArtifactView] = []
    for key_lang, key_doc, key_section in iter_translation_keys(
        workspace, doc_id=resolved_doc_id, lang=resolved_lang
    ):
        if key_doc not in sections_by_doc:
            try:
                loaded = _load_doc_sections(workspace, key_doc)
            except ReadingServiceError:
                loaded = []  # document no longer segmented → its translations are orphaned
            sections_by_doc[key_doc] = {section.id: section for section in loaded}
        section = sections_by_doc[key_doc].get(key_section)
        state = translation_state(
            workspace,
            key_lang,
            key_doc,
            key_section,
            section_content_hash(section) if section else None,
        )
        artifacts.append(_artifact_view(workspace, state, section, include_content=False))
    return SectionArtifactList(artifacts=artifacts)
