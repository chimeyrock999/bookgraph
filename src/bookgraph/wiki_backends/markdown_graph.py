from __future__ import annotations

import shutil
from pathlib import Path

from bookgraph.concepts import ConceptEntry, extract_concepts
from bookgraph.models import Section
from bookgraph.ports import WikiBackend
from bookgraph.sections import render_section_markdown
from bookgraph.wiki_backends.common import render_section_index

# Concept extraction now lives in the shared ``bookgraph.concepts`` module so the
# index stage produces the same concepts. Re-exported here for backwards
# compatibility with importers of this module.
__all__ = ["MarkdownGraphBackend", "ConceptEntry", "extract_concepts"]


class MarkdownGraphBackend(WikiBackend):
    """Compile sections into markdown pages with deterministic wiki concept links.

    This backend is intentionally stateless: it renders a book-local markdown view
    from the current document's sections only. Cross-book concept joins/backlinks
    belong to the index/query layer, not to markdown output reconciliation.
    """

    name = "markdown-graph"

    def compile_book(self, sections: list[Section], output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        sections_dir = output_dir / "sections"
        _replace_dir(sections_dir)

        concepts = extract_concepts(sections)
        concept_map = {concept.slug: concept for concept in concepts}
        concepts_by_section = _concepts_by_section(concepts)

        for section in sections:
            section_concepts = [
                concept_map[slug] for slug in concepts_by_section.get(section.id, [])
            ]
            (sections_dir / f"{section.id}.md").write_text(
                _render_section(section, section_concepts)
            )

        (output_dir / "README.md").write_text(
            _render_book_index(output_dir.name, sections, concepts)
        )
        return output_dir


def _concepts_by_section(concepts: list[ConceptEntry]) -> dict[str, list[str]]:
    by_section: dict[str, list[str]] = {}
    for concept in concepts:
        for section_id in concept.section_ids:
            by_section.setdefault(section_id, []).append(concept.slug)
    for slugs in by_section.values():
        slugs.sort()
    return by_section


def _render_book_index(doc_id: str, sections: list[Section], concepts: list[ConceptEntry]) -> str:
    base = render_section_index(doc_id, sections).rstrip()
    lines = [base, "", "## Concepts", ""]
    if concepts:
        for concept in concepts:
            lines.append(f"- {_wiki_link(concept)} ({concept.section_count} sections)")
    else:
        lines.append("No concepts extracted.")
    return "\n".join(lines) + "\n"


def _render_section(section: Section, concepts: list[ConceptEntry]) -> str:
    content = render_section_markdown(section)
    if not concepts:
        return content
    links = [_wiki_link(concept) for concept in concepts]
    return content + "\n## Linked concepts\n\n" + "\n".join(f"- {link}" for link in links) + "\n"


def _replace_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _wiki_link(concept: ConceptEntry) -> str:
    return f"[[{concept.slug}|{concept.title.replace('|', '/') }]]"
