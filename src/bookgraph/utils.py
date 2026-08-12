from __future__ import annotations

import re
from pathlib import Path

MINERU_MIDDLE_JSON_SUFFIX = "_middle.json"
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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


def validate_slug_id(value: str, *, field_name: str = "id") -> str:
    """Validate a filesystem-safe BookGraph id.

    Explicit ids and ids read from manifests are untrusted input. They must not
    contain path separators, dot-dot segments, or characters that slugify would
    rewrite into a different path.
    """

    if not ID_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field_name} must be a lowercase hyphenated slug "
            "containing only a-z, 0-9, and hyphens"
        )
    return value
