from __future__ import annotations

import re
from pathlib import Path

MINERU_MIDDLE_JSON_SUFFIX = "_middle.json"
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_url(value: str) -> bool:
    """Whether a reference points outside the workspace (a scheme-qualified or data URL).

    Shared by the parsers (which skip staging remote image refs) and the MCP asset resolver
    (which refuses to resolve them to a local file), so the two never drift apart.
    """

    return "://" in value or value.startswith("data:")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "untitled"


def unique_slug(value: str, used_slugs: set[str]) -> str:
    """Return a deterministic slug that does not collide with already-used slugs."""

    base = slugify(value)
    candidate = base
    suffix = 2
    while candidate in used_slugs:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_slugs.add(candidate)
    return candidate


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
