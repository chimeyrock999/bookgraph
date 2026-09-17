"""Resolution of a parsed block's image/table asset reference to a real file.

One resolver, used by every consumer of parsed assets: the MCP section APIs (which
hand clients an openable ``AssetRef.path``) and the ingest quality checks (which
must judge an asset exactly as a reader will see it). Two implementations of
"does this asset exist?" would let ingest and reading disagree about the same
section, which is the drift :mod:`bookgraph.quality` exists to prevent.
"""

from __future__ import annotations

import os
from pathlib import Path

from bookgraph.models import CanonicalBlock
from bookgraph.utils import is_url


def asset_reference(block: CanonicalBlock) -> str:
    """Return the block's raw asset reference, or ``""`` when it has none.

    Prefers the typed ``asset_path`` (MinerU) and falls back to the markdown
    parser's ``metadata["src"]``. A block with no reference at all (e.g. a Markdown
    table rendered inline into the section text) is not an asset file consumer.
    """

    raw = block.asset_path
    if raw:
        return raw
    meta_src = block.metadata.get("src") or block.metadata.get("asset_path")
    return str(meta_src) if meta_src else ""


def resolve_asset_path(parsed_dir: Path, block: CanonicalBlock) -> str | None:
    """Resolve a parser's asset reference to a real file under ``parsed_dir``.

    ``parsed_dir`` is the document's ``sources/parsed/<doc_id>/`` directory. The
    reference is tried under the staged ``images/`` dir that ``MinerURunner`` copies
    alongside ``document.json`` and directly under the parsed document directory, and
    the first existing regular file wins — so the location is verified on disk rather
    than guessed from whether the string contains a slash.

    Returns ``None`` — so the caller drops the asset rather than emit a bogus
    reference — unless the result is an existing regular file that stays inside the
    parsed document directory even after symlinks are followed. That rules out URLs,
    absolute paths, ``..`` traversal, symlink escapes, and references to files the
    parser never actually staged, so an ``AssetRef.path`` a client receives always
    opens a real workspace file.
    """

    raw = asset_reference(block)
    if not raw or is_url(raw):
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return None
    try:
        root_real = parsed_dir.resolve()
    except (OSError, ValueError):
        return None
    # Verify the location on disk instead of guessing from the string: try the staged
    # images/ dir first (MinerU convention) then directly under the parsed document dir.
    for base in (parsed_dir / "images", parsed_dir):
        lexical = Path(os.path.normpath(base / candidate))
        # Resolve symlinks and check containment against the real root: a lexical-only
        # check would let a symlink under images/ point outside the workspace, and would
        # accept a ".."-style path that lands on the root directory itself rather than a
        # file. Guarded because a corrupt/adversarial document.json can carry a path with
        # an embedded NUL (ValueError) — one bad asset must degrade to "no asset", not
        # crash the whole section fetch.
        try:
            real = lexical.resolve()
            if real.is_relative_to(root_real) and real.is_file():
                return str(lexical)
        except (OSError, ValueError):
            continue
    return None
