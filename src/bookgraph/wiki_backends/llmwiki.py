from __future__ import annotations

from pathlib import Path

from bookgraph.models import Section
from bookgraph.ports import WikiBackend


class LlmWikiBackend(WikiBackend):
    """Filesystem adapter that stages source sections for llm-wiki-compiler.

    The actual `llmwiki compile` process is intentionally external for now so
    this backend remains easy to test and replace.
    """

    name = "llmwiki"

    def ingest_sections(self, sections: list[Section], workspace: Path) -> None:
        sources_dir = workspace / "sources" / "sections"
        sources_dir.mkdir(parents=True, exist_ok=True)
        for section in sections:
            path = sources_dir / f"{section.id}.md"
            path.write_text(_render_section(section))


def _render_section(section: Section) -> str:
    heading = "#" * max(1, min(section.level, 6))
    frontmatter = "\n".join(
        [
            "---",
            f"id: {section.id}",
            f"doc_id: {section.doc_id}",
            f"page_start: {section.page_start}",
            f"page_end: {section.page_end}",
            "---",
            "",
        ]
    )
    return f"{frontmatter}{heading} {section.title}\n\n{section.text}\n"
