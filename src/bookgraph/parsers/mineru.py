from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from bookgraph.models import ASSET_BLOCK_TYPES, BlockType, CanonicalBlock, Document
from bookgraph.parsers.errors import UnsupportedSourceError
from bookgraph.parsers.mineru_middle_v2 import MIDDLE_V2_SCHEMA, is_middle_v2, parse_middle_v2
from bookgraph.ports import DocumentParser
from bookgraph.utils import doc_id_from_path


class MinerUMiddleJsonParser(DocumentParser):
    """Adapter for MinerU *_middle.json outputs.

    This parser intentionally consumes MinerU's structured output instead of
    invoking MinerU; :class:`bookgraph.parsers.mineru_runner.MinerURunner` owns the
    heavy external process. It reads MinerU 4's ``docvortex.middle`` 2.x contract,
    and still reads the 3.x ``pdf_info`` shape so middle JSON staged before the
    MinerU 4 migration keeps parsing.
    """

    name = "mineru-middle-json"

    def parse(self, source: Path, output_dir: Path) -> Document:
        del output_dir  # MinerU side artifacts are produced before this parser runs.
        payload = _load_json(source, self.name)
        if is_middle_v2(payload):
            try:
                return parse_middle_v2(payload, source, self.name)
            except ValueError as exc:
                raise UnsupportedSourceError(str(exc)) from exc
        pdf_info = _require_pdf_info(payload, source, self.name)

        blocks: list[CanonicalBlock] = []
        for page in pdf_info:
            page_idx = page.get("page_idx")
            para_blocks = page.get("para_blocks") or []
            for index, raw_block in enumerate(para_blocks):
                block_type = _map_mineru_block_type(str(raw_block.get("type", "unknown")))
                asset_path = (
                    _extract_asset(raw_block) if block_type in ASSET_BLOCK_TYPES else None
                )
                blocks.append(
                    CanonicalBlock(
                        id=f"p{page_idx}.b{index}",
                        type=block_type,
                        level=1 if block_type == "title" else None,
                        text=_extract_text(raw_block),
                        page_idx=page_idx,
                        bbox=tuple(raw_block["bbox"]) if "bbox" in raw_block else None,
                        asset_path=asset_path,
                        source_path=str(source),
                        order=len(blocks),
                    )
                )
        return Document(
            doc_id=doc_id_from_path(source),
            title=_document_title(blocks) or source.stem,
            blocks=blocks,
            metadata={"parser": self.name, "source_path": str(source)},
        )


def _load_json(source: Path, parser_name: str) -> Any:
    try:
        return json.loads(source.read_text())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UnsupportedSourceError(
            f"{source.name}: {parser_name} needs valid JSON: {exc}"
        ) from exc


def _require_pdf_info(payload: Any, source: Path, parser_name: str) -> list[Any]:
    """Fail loudly when the input is not MinerU middle JSON.

    Without this check a stray ``.json`` file parses into an empty document and
    the whole pipeline reports success on nothing.
    """

    pdf_info = payload.get("pdf_info") if isinstance(payload, dict) else None
    if not isinstance(pdf_info, list):
        raise UnsupportedSourceError(
            f"{source.name}: not MinerU middle JSON - {parser_name} requires a "
            f"'{MIDDLE_V2_SCHEMA}' payload (MinerU 4) or a 'pdf_info' list (MinerU 3). "
            "Run MinerU on the source first."
        )
    return pdf_info


def _document_title(blocks: list[CanonicalBlock]) -> str | None:
    return next((block.text for block in blocks if block.type == "title" and block.text), None)


def _map_mineru_block_type(value: str) -> BlockType:
    if value in {"title", "text", "list", "table", "image", "chart"}:
        return cast(BlockType, value)
    if value == "interline_equation":
        return "equation"
    return "unknown"


def _extract_asset(raw_block: dict[str, Any]) -> str | None:
    """Recover the image/table asset filename MinerU records on a block.

    MinerU stores the extracted file on the body span (``image_body``/``table_body``)
    under ``image_path`` rather than as ``content`` text, so ``_extract_text`` never
    surfaces it. Walk the nested ``blocks``/``lines``/``spans`` structure and return the
    ``image_path`` of the first *body* span — a span whose ``type`` is itself an asset
    kind — so a caption/footnote child that happens to carry an ``image_path`` key can
    never be mistaken for the block's real asset.
    """

    for line in raw_block.get("lines", []) or []:
        for span in line.get("spans", []) or []:
            if span.get("type") in ASSET_BLOCK_TYPES and (path := span.get("image_path")):
                return str(path)
    for child in raw_block.get("blocks", []) or []:
        if path := _extract_asset(child):
            return path
    return None


def _extract_text(raw_block: dict[str, Any]) -> str:
    parts: list[str] = []
    if content := raw_block.get("content"):
        parts.append(str(content))
    for line in raw_block.get("lines", []) or []:
        for span in line.get("spans", []) or []:
            if content := span.get("content"):
                parts.append(str(content))
    for child in raw_block.get("blocks", []) or []:
        child_text = _extract_text(child)
        if child_text:
            parts.append(child_text)
    return " ".join(part.strip() for part in parts if part.strip())
