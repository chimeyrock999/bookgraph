from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bookgraph.models import CanonicalBlock, Document
from bookgraph.ports import DocumentParser


class MinerUMiddleJsonParser(DocumentParser):
    """Adapter for MinerU *_middle.json outputs.

    This parser intentionally consumes MinerU's structured output instead of
    invoking MinerU. A runner plugin can be added later for the heavy external
    process.
    """

    name = "mineru-middle-json"

    def parse(self, source: Path, output_dir: Path) -> Document:
        payload = json.loads(source.read_text())
        blocks: list[CanonicalBlock] = []
        for page in payload.get("pdf_info", []):
            page_idx = page.get("page_idx")
            para_blocks = page.get("para_blocks") or []
            for index, raw_block in enumerate(para_blocks):
                block_type = _map_mineru_block_type(str(raw_block.get("type", "unknown")))
                blocks.append(
                    CanonicalBlock(
                        id=f"p{page_idx}.b{index}",
                        type=block_type,
                        level=1 if block_type == "title" else None,
                        text=_extract_text(raw_block),
                        page_idx=page_idx,
                        bbox=tuple(raw_block["bbox"]) if "bbox" in raw_block else None,
                        source_path=str(source),
                        order=len(blocks),
                    )
                )
        return Document(
            doc_id=source.stem.removesuffix("_middle"),
            title=source.stem,
            blocks=blocks,
        )


def _map_mineru_block_type(value: str) -> str:
    if value in {"title", "text", "list", "table", "image", "chart"}:
        return value
    if value == "interline_equation":
        return "equation"
    return "unknown"


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
