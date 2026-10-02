"""The MCP-side structure and asset checks of a cached section translation.

Wraps :func:`bookgraph.translation_structure.check_section_translation` and
:func:`bookgraph.translation_assets.check_translation_assets` with the registry's view
of a translation, so the translation tools (``structure_issues``, ``missing_assets``)
and reading-batch completion (``translation_structure_changed``) judge a cached body
exactly as ``bookgraph export translated-pdf`` does.
"""

from __future__ import annotations

from collections.abc import Mapping

from bookgraph.models import CanonicalBlock, Section, TranslationStructureIssue
from bookgraph.translation_assets import (
    TranslationAssetCheck,
    check_translation_assets,
    translation_link_bases,
)
from bookgraph.translation_structure import check_section_translation, local_asset_resolver
from bookgraph.translations import TranslationState, decoded_body, split_frontmatter
from bookgraph.workspace import WorkspacePaths


def _cached_body(state: TranslationState) -> str | None:
    """The cached body with frontmatter split off, or ``None`` when there is none or it
    is not UTF-8."""

    body = decoded_body(state)
    return split_frontmatter(body)[1] if body is not None else None


def translation_structure_issues(
    workspace: WorkspacePaths,
    state: TranslationState,
    section: Section | None,
    blocks: Mapping[str, CanonicalBlock],
) -> list[TranslationStructureIssue]:
    """Structural targets the cached body changed relative to the section it translates.

    ``structure_issues`` lists the link destinations, image paths, reference
    definitions, HTML anchors, and heading ids the body dropped or added; a translation
    should translate prose and labels but keep every structural target byte-for-byte,
    so a non-empty list means the body needs fixing. Judged as the translated export
    judges it: frontmatter is split off, the source is rebuilt from the parsed
    ``blocks`` (code re-fenced), and image paths resolve next to the body, then under
    the parsed document. No section, no body, or a non-UTF-8 body has nothing to check.
    """

    body = _cached_body(state)
    if section is None or body is None:
        return []
    parsed_dir = workspace.sources_parsed / section.doc_id
    resolves = local_asset_resolver(
        workspace.root, translation_link_bases(workspace.root, parsed_dir, state.paths.body.parent)
    )
    return check_section_translation(section, body, blocks=blocks, asset_resolves=resolves)


def translation_asset_check(
    workspace: WorkspacePaths,
    state: TranslationState,
    section: Section | None,
    blocks: Mapping[str, CanonicalBlock],
) -> TranslationAssetCheck | None:
    """Which of the section's staged figures/tables the cached body leaves out.

    ``None`` when there is no section or no readable body to check.
    """

    body = _cached_body(state)
    if section is None or body is None:
        return None
    return check_translation_assets(
        section,
        body,
        blocks=blocks,
        root=workspace.root,
        parsed_dir=workspace.sources_parsed / section.doc_id,
        body_dir=state.paths.body.parent,
    )
