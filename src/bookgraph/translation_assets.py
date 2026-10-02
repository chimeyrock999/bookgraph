"""Check that a translation carries its section's figures and tables.

The section content hash covers a section's title and text only, so a re-parse that
newly stages a figure leaves a prose-only translation ``fresh``. Whether a translation
is complete therefore cannot rest on the writer's ``includes_assets`` claim: it is
verified from the body. Every *staged* asset block of the section — one whose file
resolves under the parsed document, i.e. one ``get_section`` hands out as an
``AssetRef`` — must be linked by an image in the body (a Markdown image or an
``<img src>``) that resolves to the same file, wherever the link is written from
(``AssetRef.link``, or a workspace-relative path). An asset whose file was never
staged cannot be linked or checked, so for those the writer's claim still stands;
``asset_missing`` and ``bookgraph assets repair`` own them.

:func:`check_translation_assets` is the one entry point the translation tools and the
translated export share, so they judge the same body the same way.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bookgraph.assets import asset_link, asset_reference, resolve_asset_path
from bookgraph.models import (
    ASSET_BLOCK_TYPES,
    CanonicalBlock,
    MissingTranslationAsset,
    Section,
)
from bookgraph.translation_structure import resolve_translation_image, structural_targets


@dataclass(frozen=True)
class TranslationAssetCheck:
    """What a translation body carries of its section's figures/tables.

    ``missing`` lists the staged assets the body does not link; ``unverified`` is
    whether the section also has asset blocks whose file was never staged, which no
    body can be checked against.
    """

    missing: list[MissingTranslationAsset]
    unverified: bool = False

    def includes_assets(self, claimed: bool) -> bool:
        """Whether the translation is complete: every staged asset is linked, and the
        writer ``claimed`` the unstaged ones (if any) were carried too."""

        return not self.missing and (claimed or not self.unverified)


def translation_link_bases(root: Path, parsed_dir: Path, body_dir: Path) -> list[Path]:
    """Where a translation's relative image links resolve, in the export's order: next
    to the body, then under the parsed document, then the workspace root."""

    return [body_dir, parsed_dir / "images", parsed_dir, root]


def check_translation_assets(
    section: Section,
    body: str,
    *,
    blocks: Mapping[str, CanonicalBlock],
    root: Path,
    parsed_dir: Path,
    body_dir: Path,
) -> TranslationAssetCheck:
    """Check a translation ``body`` (frontmatter split off) against ``section``'s assets.

    ``blocks`` are the parsed document's blocks by id, ``parsed_dir`` its
    ``sources/parsed/<doc_id>/`` directory, and ``body_dir`` the directory the
    translation body lives in (its relative links resolve there first).
    """

    staged: list[tuple[CanonicalBlock, str]] = []
    unverified = False
    for block_id in section.block_ids:
        block = blocks.get(block_id)
        if block is None or block.type not in ASSET_BLOCK_TYPES or not asset_reference(block):
            continue
        path = resolve_asset_path(parsed_dir, block)
        if path is None:
            unverified = True
        else:
            staged.append((block, path))
    if not staged:
        return TranslationAssetCheck(missing=[], unverified=unverified)
    bases = translation_link_bases(root, parsed_dir, body_dir)
    linked = {
        resolved
        for kind, target in structural_targets(body)
        if kind == "image"
        and (resolved := resolve_translation_image(root, target, bases)) is not None
    }
    missing = [
        MissingTranslationAsset(
            block_id=block.id,
            type=block.type,
            link=asset_link(parsed_dir, path),
            reference=asset_reference(block),
            caption=block.text,
        )
        for block, path in staged
        if Path(path).resolve() not in linked
    ]
    return TranslationAssetCheck(missing=missing, unverified=unverified)


def describe_missing_assets(missing: list[MissingTranslationAsset], limit: int = 5) -> str:
    """A one-line summary of ``missing`` for warnings and error messages."""

    parts = [f"{asset.type} {asset.block_id} ({asset.link})" for asset in missing[:limit]]
    if len(missing) > limit:
        parts.append(f"and {len(missing) - limit} more")
    return ", ".join(parts)
