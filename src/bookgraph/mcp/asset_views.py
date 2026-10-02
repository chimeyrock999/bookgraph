"""MCP view models for a section's figure/table assets.

Re-exported by :mod:`bookgraph.mcp.service`, so ``service.AssetRef`` keeps working.
"""

from __future__ import annotations

from pydantic import BaseModel


class AssetRef(BaseModel):
    """A figure/table asset that belongs to a section, resolved to a real file.

    ``caption`` is the block's text (a MinerU image/table block surfaces only its
    caption as text — the labels/data live inside ``path``). ``order`` is the block's
    position in the parsed document, so a client can place the asset relative to the
    section's prose.

    ``type`` is the parser's classification, kept verbatim. Layout parsers do confuse
    figures with tables, so it travels with ``type_confidence`` (how well the caption's
    label corroborates it) and, when the caption contradicts the parser,
    ``suggested_type`` — the type the caption implies. A client can then trust, correct,
    or open the file instead of taking a silently wrong ``type`` at face value.

    ``path`` is the absolute file to *open*. ``link`` is the same file relative to the
    document's ``sources/parsed/<doc_id>/`` directory (e.g. ``images/fig1-1.png``) —
    the reference to *write* into a translation (``![caption](<link>)``): it stays
    valid when the workspace moves, and the export resolves it.
    """

    block_id: str
    type: str
    path: str
    link: str = ""
    caption: str = ""
    order: int | None = None
    page_idx: int | None = None
    type_confidence: float = 1.0
    suggested_type: str | None = None
