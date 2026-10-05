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

Inline spans are joined verbatim, as MinerU's own Markdown renderer joins them, and
hyperlinks stay Markdown links; a block's ``anchor`` is kept in its metadata so
internal links have targets. For an EPUB that anchor is an id MinerU assigns
(``epub-<hash>``, which its internal ``#…`` links are rewritten to), not the source
element's id; a DOCX bookmark name is kept as written. A table
MinerU read natively (EPUB/DOCX) has no image, only its HTML, so that HTML is the
block text and keeps colspan, rowspan and nested tables.

Non-PDF sources (MinerU's ``flash``-only formats) get two more rules:

- provenance from the source map :class:`MinerURunner` stages next to the middle
  JSON (:mod:`bookgraph.parsers.mineru_source_map`): an EPUB block records the spine
  member its page came from as ``<source.epub>!<member>``;
- DOCX heading levels are normalized. MinerU reserves level 1 for the ``Title``
  style and maps ``Heading N`` to level N+1, while MarkItDown maps ``Heading N`` to
  N; the reader shifts DOCX section titles back by one so switching adapters does
  not change the sections.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path
from typing import Any

from bookgraph.models import BlockType, CanonicalBlock, Document
from bookgraph.parsers.mineru_source_map import read_source_map
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
_VISUAL_TYPES = frozenset({"image", "table", "chart"})

# Inline span types of the ``docvortex.middle`` contract. Any other ``content`` list
# item is a nested child block (a list item, an index entry).
_INLINE_SPAN_TYPES = frozenset({"text", "hyperlink", "equation_inline", "code_inline"})


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

    metadata = payload.get("metadata") or {}
    file_suffix = str(metadata.get("file_suffix") or "pdf").lower()
    provenance = _Provenance(read_source_map(source) if file_suffix != "pdf" else None)
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
            asset_path = _asset_path(raw_block)
            block_metadata: dict[str, str | int | float | bool | None] = {"mineru_type": raw_type}
            if isinstance(anchor := raw_block.get("anchor"), str) and anchor:
                block_metadata["anchor"] = anchor
            block_metadata.update(provenance.locate(page_idx, asset_path))
            blocks.append(
                CanonicalBlock(
                    id=f"p{page_idx}.b{block_index}",
                    type=block_type,
                    level=(_title_level(raw_block, file_suffix) if block_type == "title" else None),
                    text=_block_text(raw_block) or _uncaptioned_image_text(raw_block, file_suffix),
                    page_idx=page_idx,
                    bbox=tuple(bbox) if isinstance(bbox, list) and len(bbox) == 4 else None,
                    asset_path=asset_path,
                    source_path=str(source),
                    order=len(blocks),
                    source_html=_source_html(raw_block, raw_type, block_type),
                    metadata=block_metadata,
                )
            )

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
            "mineru_file_suffix": file_suffix,
            **provenance.document_metadata(),
        },
    )


class _Provenance:
    """Where a block of a non-PDF source came from, read from the staged source map."""

    def __init__(self, source_map: dict[str, Any] | None) -> None:
        source_map = source_map or {}
        source = source_map.get("source")
        spine = source_map.get("spine")
        images = source_map.get("images")
        self.source = source if isinstance(source, str) else None
        self.spine = (
            [m if isinstance(m, str) else None for m in spine] if isinstance(spine, list) else None
        )
        self.images = (
            {str(k): str(v) for k, v in images.items()} if isinstance(images, dict) else {}
        )

    def locate(
        self, page_idx: object, asset_path: str | None
    ) -> dict[str, str | int | float | bool | None]:
        located: dict[str, str | int | float | bool | None] = {}
        if self.source is None:
            return located
        spine = self.spine or []
        # An itemref the manifest does not name is a page with no member (``None``).
        member = (
            spine[page_idx] if isinstance(page_idx, int) and 0 <= page_idx < len(spine) else None
        )
        if member is not None:
            located["source_member"] = member
            located["source_locator"] = f"{self.source}!{member}"
        if asset_path and (asset_member := self.images.get(asset_path)):
            located["asset_source_member"] = asset_member
        return located

    def document_metadata(self) -> dict[str, str | int | float | bool | None]:
        return {"source_name": self.source} if self.source is not None else {}


def _title_level(raw_block: dict[str, Any], file_suffix: str) -> int:
    level = raw_block.get("level")
    level = level if isinstance(level, int) and level >= 1 else 1
    if file_suffix == "docx" and raw_block.get("type") == "paragraph_title":
        # MinerU keeps level 1 for the Title style: Heading N arrives as N+1.
        return max(level - 1, 1)
    return level


def _asset_path(raw_block: dict[str, Any]) -> str | None:
    for child in _children(raw_block):
        if child.get("type") in _VISUAL_BODY_TYPES and (path := child.get("image_path")):
            return str(path)
    return None


def _block_text(raw_block: dict[str, Any]) -> str:
    content = raw_block.get("content")
    if isinstance(content, str):
        return content.strip()
    if raw_block.get("type") in _VISUAL_TYPES:
        return _visual_text(raw_block)
    return _content_text(content or [])


def _visual_text(raw_block: dict[str, Any]) -> str:
    """Captions and footnotes, after the table HTML when there is no rendered asset.

    A PDF table or figure keeps its image on the ``*_body`` child, so its text is the
    caption. A table MinerU read natively (EPUB/DOCX) has only HTML; dropping it would
    leave an empty block, so the HTML is kept as the block's text.
    """

    children = _children(raw_block)
    notes = " ".join(
        text
        for text in (_block_text(c) for c in children if c.get("type") not in _VISUAL_BODY_TYPES)
        if text
    )
    body = next((c for c in children if c.get("type") in _VISUAL_BODY_TYPES), None)
    body_text = body.get("content") if body is not None else None
    body_text = body_text.strip() if isinstance(body_text, str) else ""
    if body is not None and not body.get("image_path") and raw_block.get("type") == "table":
        return "\n\n".join(part for part in (body_text, notes) if part)
    return notes


def _uncaptioned_image_text(raw_block: dict[str, Any], file_suffix: str) -> str:
    """A non-PDF picture's own text (a DOCX picture's name) when it has no caption.

    For a PDF the body text is whatever was read inside the figure (a cover's or a
    diagram's lettering), not a description of it, so PDFs keep no text here.
    """

    if file_suffix == "pdf" or raw_block.get("type") != "image":
        return ""
    for child in _children(raw_block):
        if child.get("type") == "image_body" and isinstance(text := child.get("content"), str):
            return text.strip()
    return ""


def _content_text(items: list[Any]) -> str:
    """Join a block's content: inline spans verbatim, nested child blocks by a space."""

    parts: list[str] = []
    run: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") in _INLINE_SPAN_TYPES and (
            isinstance(item.get("content"), str) or item.get("type") == "hyperlink"
        ):
            run.append(_inline_text(item))
            continue
        parts.append("".join(run).strip())
        run = []
        parts.append(_block_text(item))
    parts.append("".join(run).strip())
    return " ".join(part for part in parts if part)


def _inline_text(span: dict[str, Any]) -> str:
    """Render one inline span the way MinerU's Markdown output writes it."""

    span_type = span.get("type")
    content = span.get("content")
    if span_type == "hyperlink":
        label = "".join(
            _inline_text(child) for child in content or [] if isinstance(child, dict)
        ).strip()
        url = span.get("url")
        if not label or not isinstance(url, str) or not url or url == ".":
            return label
        return f"[{_escape_link_label(label)}]({_escape_link_url(url)})"
    if not isinstance(content, str):
        return ""
    if span_type == "equation_inline":
        return f"${content}$"
    if span_type == "code_inline":
        return f"`{content}`"
    return content


def _source_html(raw_block: dict[str, Any], raw_type: str, block_type: BlockType) -> str | None:
    text = _block_text(raw_block)
    if raw_type == "code" and text:
        return f"<pre><code>{escape(text)}</code></pre>"
    if block_type not in {"text", "list"}:
        return None
    inline = _content_html(raw_block.get("content") or [])
    if not inline:
        return None
    tag = "li" if block_type == "list" else "p"
    return f"<{tag}>{inline}</{tag}>"


def _content_html(items: list[Any]) -> str:
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") in _INLINE_SPAN_TYPES:
            parts.append(_inline_html(item))
        else:
            nested = _content_html(item.get("content") or [])
            if nested:
                parts.append(nested)
    return "".join(parts).strip()


def _inline_html(span: dict[str, Any]) -> str:
    span_type = span.get("type")
    content = span.get("content")
    if span_type == "hyperlink":
        label = _content_html(content or [])
        url = span.get("url")
        if not label or not isinstance(url, str) or not url or url == ".":
            return label
        return f'<a href="{escape(url, quote=True)}">{label}</a>'
    if not isinstance(content, str):
        return ""
    if span_type == "code_inline":
        return f"<code>{escape(content)}</code>"
    return escape(content)


def _escape_link_label(label: str) -> str:
    return re.sub(r"(?<!\\)([\[\]])", r"\\\1", label)


def _escape_link_url(url: str) -> str:
    return url.replace("\\", "%5C").replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _children(raw_block: dict[str, Any]) -> list[dict[str, Any]]:
    content = raw_block.get("content")
    if not isinstance(content, list):
        return []
    return [child for child in content if isinstance(child, dict)]
