from __future__ import annotations

from bookgraph.models import Section


def render_section_index(doc_id: str, sections: list[Section]) -> str:
    """Render a book-local section index in reading order."""

    lines = [f"# {doc_id}", ""]
    for section in sections:
        indent = "  " * max(section.level - 1, 0)
        lines.append(f"{indent}- [{section.title}](sections/{section.id}.md)")
    return "\n".join(lines) + "\n"
