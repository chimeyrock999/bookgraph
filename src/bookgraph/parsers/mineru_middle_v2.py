"""Read MinerU 4's shared result contract (``docvortex.middle`` schema 2.x).

MinerU 4 replaced the 3.x ``pdf_info``/``para_blocks`` middle JSON with a versioned
schema shared by every tier and input format::

    {"schema": "docvortex.middle", "schema_version": "2.0",
     "metadata": {"file_suffix": "pdf", "producer": {...}, "document": {...}},
     "extensions": {"mineru": {"tier": "basic", ...}},
     "pages": [{"page_idx": 0, "blocks": [{"type": ..., "index": 3, ...}]}]}

Each page block carries a page-local ``index`` that MinerU also uses in its stable
locators (``.../page:{page}/block:{index}``), so canonical block ids keep it
(``p{page}.b{index}``) instead of renumbering. Running headers, footers, page
numbers and margin notes are page furniture, not reading content, and are dropped
the way 3.x moved them to ``discarded_blocks``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bookgraph.models import BlockType, CanonicalBlock, Document
from bookgraph.utils import doc_id_from_path

MIDDLE_V2_SCHEMA = "docvortex.middle"
_SUPPORTED_SCHEMA_MAJOR = "2"

# MinerU's own PAGE_AUXILIARY_BLOCK_TYPES: decoration around the page body.
_PAGE_FURNITURE = frozenset({"header", "footer", "page_number", "aside_text"})

_TYPE_MAP: dict[str, BlockType] = {
    "doc_title": "title",
    "paragraph_title": "title",
    "text": "text",
    "ref_text": "text",
    "page_footnote": "text",
    "code": "text",
    "index": "text",
    "list": "list",
    "equation": "equation",
    "image": "image",
    "table": "table",
    "chart": "chart",
}

# Visual blocks keep their file on the ``*_body`` child; captions and footnotes are
# the human-readable text, matching what the 3.x adapter surfaced for assets.
_VISUAL_BODY_TYPES = frozenset({"image_body", "table_body", "chart_body"})


def is_middle_v2(payload: object) -> bool:
    return isinstance(payload, dict) and payload.get("schema") == MIDDLE_V2_SCHEMA


def parse_middle_v2(payload: dict[str, Any], source: Path, parser_name: str) -> Document:
    """Map a ``docvortex.middle`` payload onto canonical blocks.

    Raises ``ValueError`` for a schema major version this adapter does not know, so
    a future MinerU contract change fails loudly instead of parsing to nothing.
    """

    version = str(payload.get("schema_version", ""))
    if version.split(".")[0] != _SUPPORTED_SCHEMA_MAJOR:
        raise ValueError(
            f"{source.name}: unsupported {MIDDLE_V2_SCHEMA} schema_version '{version}'; "
            f"{parser_name} reads {_SUPPORTED_SCHEMA_MAJOR}.x."
        )
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise ValueError(f"{source.name}: {MIDDLE_V2_SCHEMA} payload has no 'pages' list.")

    blocks: list[CanonicalBlock] = []
    for page in pages:
        page_idx = page.get("page_idx")
        for position, raw_block in enumerate(page.get("blocks") or []):
            raw_type = str(raw_block.get("type", "unknown"))
            if raw_type in _PAGE_FURNITURE:
                continue
            block_type = _TYPE_MAP.get(raw_type, "unknown")
            index = raw_block.get("index")
            block_index = index if isinstance(index, int) else position
            bbox = raw_block.get("bbox")
            blocks.append(
                CanonicalBlock(
                    id=f"p{page_idx}.b{block_index}",
                    type=block_type,
                    level=_title_level(raw_block) if block_type == "title" else None,
                    text=_block_text(raw_block),
                    page_idx=page_idx,
                    bbox=tuple(bbox) if isinstance(bbox, list) and len(bbox) == 4 else None,
                    asset_path=_asset_path(raw_block),
                    source_path=str(source),
                    order=len(blocks),
                    metadata={"mineru_type": raw_type},
                )
            )

    metadata = payload.get("metadata") or {}
    mineru = (payload.get("extensions") or {}).get("mineru") or {}
    producer = metadata.get("producer") or {}
    document_props = metadata.get("document") or {}
    title = document_props.get("title") or next(
        (block.text for block in blocks if block.type == "title" and block.text), None
    )
    return Document(
        doc_id=doc_id_from_path(source),
        title=title or source.stem,
        blocks=blocks,
        metadata={
            "parser": parser_name,
            "source_path": str(source),
            "mineru_schema": f"{MIDDLE_V2_SCHEMA}/{version}",
            "mineru_version": producer.get("version"),
            "mineru_tier": mineru.get("tier"),
        },
    )


def _title_level(raw_block: dict[str, Any]) -> int:
    level = raw_block.get("level")
    return level if isinstance(level, int) and level >= 1 else 1


def _asset_path(raw_block: dict[str, Any]) -> str | None:
    for child in _children(raw_block):
        if child.get("type") in _VISUAL_BODY_TYPES and (path := child.get("image_path")):
            return str(path)
    return None


def _block_text(raw_block: dict[str, Any]) -> str:
    content = raw_block.get("content")
    if isinstance(content, str):
        return content.strip()
    if raw_block.get("type") in {"image", "table", "chart"}:
        # The body holds the asset (and table HTML); captions/footnotes are the text.
        parts = [
            _block_text(child)
            for child in _children(raw_block)
            if child.get("type") not in _VISUAL_BODY_TYPES
        ]
    else:
        parts = [_inline_text(item) for item in content or [] if isinstance(item, dict)]
    return " ".join(part for part in parts if part)


def _inline_text(item: dict[str, Any]) -> str:
    """Flatten one inline span or nested child block to plain text."""

    content = item.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        # Hyperlink spans and list/index/code children nest further content.
        return " ".join(
            text
            for text in (_inline_text(child) for child in content if isinstance(child, dict))
            if text
        )
    return ""


def _children(raw_block: dict[str, Any]) -> list[dict[str, Any]]:
    content = raw_block.get("content")
    if not isinstance(content, list):
        return []
    return [child for child in content if isinstance(child, dict)]
