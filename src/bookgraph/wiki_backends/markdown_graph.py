from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bookgraph.models import Section
from bookgraph.ports import WikiBackend
from bookgraph.sections import render_section_markdown
from bookgraph.utils import unique_slug

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "with",
}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")


@dataclass(frozen=True)
class ConceptEntry:
    slug: str
    title: str
    count: int
    section_ids: list[str]


class MarkdownGraphBackend(WikiBackend):
    """Compile sections into a linked markdown wiki with deterministic concepts."""

    name = "markdown-graph"

    def compile_book(self, sections: list[Section], output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        sections_dir = output_dir / "sections"
        concepts_dir = output_dir.parent.parent / "concepts"
        sections_dir.mkdir(parents=True, exist_ok=True)
        concepts_dir.mkdir(parents=True, exist_ok=True)

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

        for concept in concepts:
            (concepts_dir / f"{concept.slug}.md").write_text(_render_concept(concept, sections))

        (output_dir / "README.md").write_text(
            _render_book_index(output_dir.name, sections, concepts)
        )
        return output_dir


def extract_concepts(sections: list[Section], *, max_concepts: int = 50) -> list[ConceptEntry]:
    """Extract deterministic lightweight concepts from section titles/headings/text.

    This intentionally avoids LLMs/embeddings: concepts are high-signal repeated
    title-case or long domain terms. It is a first linked-wiki backend, not a
    semantic concept miner.
    """

    occurrences: dict[str, list[str]] = {}
    display_titles: dict[str, str] = {}
    counts: Counter[str] = Counter()
    used_slugs: set[str] = set()

    for section in sections:
        candidates = _candidate_terms(section)
        seen_in_section: set[str] = set()
        for candidate in candidates:
            key = candidate.lower()
            if key in _STOPWORDS:
                continue
            if len(key) < 3:
                continue
            counts[key] += 1
            display_titles.setdefault(key, _display_title(candidate))
            if key not in seen_in_section:
                occurrences.setdefault(key, []).append(section.id)
                seen_in_section.add(key)

    ranked = sorted(
        counts,
        key=lambda key: (-counts[key], display_titles[key].lower(), key),
    )[:max_concepts]

    concepts: list[ConceptEntry] = []
    for key in ranked:
        title = display_titles[key]
        slug = unique_slug(title, used_slugs)
        concepts.append(
            ConceptEntry(
                slug=slug,
                title=title,
                count=counts[key],
                section_ids=occurrences[key],
            )
        )
    return concepts


def _candidate_terms(section: Section) -> list[str]:
    candidates: list[str] = []
    candidates.extend(section.heading_path)
    candidates.append(section.title)
    candidates.extend(_title_case_phrases(section.text))
    candidates.extend(
        token
        for token in _TOKEN_RE.findall(section.text)
        if len(token) >= 6 and token.lower() not in _STOPWORDS
    )
    return candidates


def _title_case_phrases(text: str) -> list[str]:
    words = re.findall(r"[A-Z][A-Za-z0-9-]+", text)
    phrases: list[str] = []
    current: list[str] = []
    for word in words:
        if word.lower() in _STOPWORDS:
            if current:
                phrases.append(" ".join(current))
                current = []
            continue
        current.append(word)
        if len(current) == 3:
            phrases.append(" ".join(current))
            current = []
    if current:
        phrases.append(" ".join(current))
    return phrases


def _display_title(candidate: str) -> str:
    words = [word for word in re.split(r"\s+", candidate.strip()) if word]
    if not words:
        return "Untitled"
    if len(words) == 1:
        return words[0]
    return " ".join(words[:4])


def _concepts_by_section(concepts: list[ConceptEntry]) -> dict[str, list[str]]:
    by_section: dict[str, list[str]] = {}
    for concept in concepts:
        for section_id in concept.section_ids:
            by_section.setdefault(section_id, []).append(concept.slug)
    for slugs in by_section.values():
        slugs.sort()
    return by_section


def _render_book_index(doc_id: str, sections: list[Section], concepts: list[ConceptEntry]) -> str:
    lines = [f"# {doc_id}", "", "## Sections", ""]
    for section in sections:
        indent = "  " * max(section.level - 1, 0)
        lines.append(f"{indent}- [{section.title}](sections/{section.id}.md)")
    lines.extend(["", "## Concepts", ""])
    if concepts:
        for concept in concepts:
            lines.append(
                f"- [{concept.title}](../../concepts/{concept.slug}.md) "
                f"({len(concept.section_ids)} sections)"
            )
    else:
        lines.append("No concepts extracted.")
    return "\n".join(lines) + "\n"


def _render_section(section: Section, concepts: list[ConceptEntry]) -> str:
    content = render_section_markdown(section)
    if not concepts:
        return content
    links = [f"[[{concept.slug}|{concept.title}]]" for concept in concepts]
    return content + "\n## Linked concepts\n\n" + "\n".join(f"- {link}" for link in links) + "\n"


def _render_concept(concept: ConceptEntry, sections: list[Section]) -> str:
    by_id = {section.id: section for section in sections}
    lines = [
        "---",
        f"concept: {concept.slug!r}",
        f"title: {concept.title!r}",
        f"count: {concept.count}",
        "---",
        "",
        f"# {concept.title}",
        "",
        "## Mentioned in",
        "",
    ]
    for section_id in concept.section_ids:
        section = by_id.get(section_id)
        title = section.title if section is not None else section_id
        doc_id = section.doc_id if section is not None else section_id.split(".")[0]
        lines.append(f"- [{title}](../books/{doc_id}/sections/{section_id}.md)")
    return "\n".join(lines) + "\n"
