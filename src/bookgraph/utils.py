from __future__ import annotations

import re
from pathlib import Path

MINERU_MIDDLE_JSON_SUFFIX = "_middle.json"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "untitled"


def doc_id_from_path(source: Path) -> str:
    """Derive a stable doc id from a source path, ignoring parser-specific suffixes."""

    name = source.name
    stem = (
        name[: -len(MINERU_MIDDLE_JSON_SUFFIX)]
        if name.lower().endswith(MINERU_MIDDLE_JSON_SUFFIX)
        else source.stem
    )
    return slugify(stem)
