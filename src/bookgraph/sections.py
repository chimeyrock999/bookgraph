from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bookgraph.models import Section


@dataclass(frozen=True)
class SectionsOutput:
    """Paths written by :func:`write_sections`."""

    manifest: Path
    markdown: list[Path]


def write_sections(sections: list[Section], output_dir: Path) -> SectionsOutput:
    """Persist reading sections under ``sources/sections/<doc_id>/``.

    Writes the canonical machine-readable manifest ``sections.jsonl`` (one
    ``Section`` per line) plus one human-readable ``<section_id>.md`` reading unit
    per section. Section ids double as filenames, so duplicate ids are refused up
    front rather than silently overwriting each other's Markdown.
    """

    _reject_duplicate_ids(sections)

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "sections.jsonl"
    manifest.write_text("".join(section.model_dump_json() + "\n" for section in sections))

    markdown: list[Path] = []
    for section in sections:
        path = output_dir / f"{section.id}.md"
        path.write_text(render_section_markdown(section))
        markdown.append(path)

    return SectionsOutput(manifest=manifest, markdown=markdown)


def render_section_markdown(section: Section) -> str:
    """Render a section as Markdown with YAML frontmatter carrying provenance.

    Frontmatter values are emitted as JSON scalars/arrays, which are valid YAML,
    so titles containing colons or quotes cannot corrupt the frontmatter.
    """

    fields: dict[str, object] = {
        "id": section.id,
        "doc_id": section.doc_id,
        "title": section.title,
        "level": section.level,
        "heading_path": section.heading_path,
        "page_start": section.page_start,
        "page_end": section.page_end,
        "prev_id": section.prev_id,
        "next_id": section.next_id,
        "block_ids": section.block_ids,
    }
    lines = ["---"]
    lines += [f"{key}: {json.dumps(value)}" for key, value in fields.items()]
    lines.append("---")

    heading = "#" * max(1, min(section.level, 6))
    body = f"{heading} {section.title}"
    if section.text:
        body += f"\n\n{section.text}"
    return "\n".join(lines) + "\n\n" + body + "\n"


def _reject_duplicate_ids(sections: list[Section]) -> None:
    counts = Counter(section.id for section in sections)
    duplicates = sorted(section_id for section_id, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(
            "duplicate section ids would overwrite each other: " + ", ".join(duplicates)
        )
