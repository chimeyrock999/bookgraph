"""The MCP-side structure check of a cached section translation.

Wraps :func:`bookgraph.translation_structure.check_section_translation` with the
registry's view of a translation, so the translation tools (``structure_issues``) and
reading-batch completion (``translation_structure_changed``) judge a cached body
exactly as ``bookgraph export translated-pdf`` does.
"""

from __future__ import annotations

from collections.abc import Mapping

from bookgraph.models import CanonicalBlock, Section, TranslationStructureIssue
from bookgraph.translation_structure import check_section_translation, local_asset_resolver
from bookgraph.translations import TranslationState, split_frontmatter
from bookgraph.workspace import WorkspacePaths


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

    if section is None or state.body is None:
        return []
    try:
        body = state.body.decode("utf-8")
    except UnicodeDecodeError:
        return []
    parsed_dir = workspace.sources_parsed / section.doc_id
    resolves = local_asset_resolver(
        workspace.root,
        [state.paths.body.parent, parsed_dir / "images", parsed_dir, workspace.root],
    )
    _, body = split_frontmatter(body)
    return check_section_translation(section, body, blocks=blocks, asset_resolves=resolves)
