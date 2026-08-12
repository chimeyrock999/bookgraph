from __future__ import annotations

import json
from pathlib import Path

from bookgraph.models import Section
from bookgraph.ports import WikiBackend
from bookgraph.sections import render_section_markdown


class LlmWikiBackend(WikiBackend):
    """Filesystem adapter that compiles section manifests into a book wiki tree."""

    name = "llmwiki"

    def compile_book(self, sections: list[Section], output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        sections_dir = output_dir / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        for section in sections:
            (sections_dir / f"{section.id}.md").write_text(render_section_markdown(section))

        index_path = output_dir / "README.md"
        index_path.write_text(_render_index(output_dir.name, sections))
        return output_dir


def read_sections_manifest(manifest: Path) -> list[Section]:
    sections: list[Section] = []
    for line_number, raw_line in enumerate(manifest.read_text().splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            sections.append(Section.model_validate(json.loads(raw_line)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid section in {manifest}:{line_number}: {exc}") from exc
    return sections


def _render_index(doc_id: str, sections: list[Section]) -> str:
    lines = [f"# {doc_id}", ""]
    for section in sections:
        indent = "  " * max(section.level - 1, 0)
        lines.append(f"{indent}- [{section.title}](sections/{section.id}.md)")
    return "\n".join(lines) + "\n"
