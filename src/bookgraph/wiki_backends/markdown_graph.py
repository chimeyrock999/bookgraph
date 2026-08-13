from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bookgraph.models import Section
from bookgraph.ports import WikiBackend
from bookgraph.sections import read_sections, render_section_markdown
from bookgraph.utils import unique_slug
from bookgraph.wiki_backends.common import render_section_index

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
_CONCEPTS_MARKER = "<!-- bookgraph:concepts -->"


@dataclass(frozen=True)
class ConceptEntry:
    slug: str
    title: str
    section_ids: list[str]

    @property
    def section_count(self) -> int:
        return len(self.section_ids)


class MarkdownGraphBackend(WikiBackend):
    """Compile sections into a linked markdown wiki with deterministic concepts."""

    name = "markdown-graph"

    def compile_book(
        self,
        sections: list[Section],
        output_dir: Path,
        concepts_dir: Path | None = None,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        resolved_concepts_dir = concepts_dir or output_dir / "concepts"
        return self.compile_book_with_concepts(sections, output_dir, resolved_concepts_dir)

    def compile_book_with_concepts(
        self,
        sections: list[Section],
        output_dir: Path,
        concepts_dir: Path,
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        concepts_dir.mkdir(parents=True, exist_ok=True)
        sections_dir = output_dir / "sections"
        _replace_dir(sections_dir)

        concepts = extract_concepts(sections)
        current_slugs = {concept.slug for concept in concepts}
        concept_map = {concept.slug: concept for concept in concepts}
        concepts_by_section = _concepts_by_section(concepts)

        for section in sections:
            section_concepts = [
                concept_map[slug] for slug in concepts_by_section.get(section.id, [])
            ]
            (sections_dir / f"{section.id}.md").write_text(
                _render_section(section, section_concepts)
            )

        _remove_stale_mentions(concepts_dir, _doc_id(sections), current_slugs)
        for concept in concepts:
            concept_path = concepts_dir / f"{concept.slug}.md"
            existing = _read_existing_concept_page(concept_path)
            sections_by_doc = dict(existing.sections_by_doc)
            sections_by_doc[_doc_id(sections)] = list(concept.section_ids)
            (concepts_dir / f"{concept.slug}.md").write_text(
                _render_concept(concept, sections_by_doc, concepts_dir)
            )

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
        key=lambda key: (-len(occurrences[key]), display_titles[key].lower(), key),
    )[:max_concepts]

    concepts: list[ConceptEntry] = []
    for key in ranked:
        title = display_titles[key]
        slug = unique_slug(title, used_slugs)
        concepts.append(
            ConceptEntry(
                slug=slug,
                title=title,
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
    base = render_section_index(doc_id, sections).rstrip()
    lines = [base, "", "## Concepts", ""]
    if concepts:
        for concept in concepts:
            title = _escape_markdown_link_text(concept.title)
            lines.append(
                f"- [{title}](../../concepts/{concept.slug}.md) ({concept.section_count} sections)"
            )
    else:
        lines.append("No concepts extracted.")
    return "\n".join(lines) + "\n"


def _render_section(section: Section, concepts: list[ConceptEntry]) -> str:
    content = render_section_markdown(section)
    if not concepts:
        return content
    links = [_wiki_link(concept) for concept in concepts]
    return content + "\n## Linked concepts\n\n" + "\n".join(f"- {link}" for link in links) + "\n"


def _render_concept(
    concept: ConceptEntry,
    sections_by_doc: dict[str, list[str]],
    concepts_dir: Path,
) -> str:
    lines = [
        "---",
        f"concept: {json.dumps(concept.slug)}",
        f"title: {json.dumps(concept.title)}",
        f"section_count: {sum(len(section_ids) for section_ids in sections_by_doc.values())}",
        "---",
        "",
        f"# {concept.title}",
        "",
        _CONCEPTS_MARKER,
        json.dumps(sections_by_doc, sort_keys=True),
        _CONCEPTS_MARKER,
        "",
        "## Mentioned in",
        "",
    ]
    for doc_id in sorted(sections_by_doc):
        sections = _read_compiled_sections_for_doc(concepts_dir, doc_id)
        by_id = {section.id: section for section in sections}
        for section_id in sections_by_doc[doc_id]:
            section = by_id[section_id]
            title = _escape_markdown_link_text(section.title)
            lines.append(f"- [{title}](../books/{doc_id}/sections/{section_id}.md)")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class _ExistingConceptPage:
    sections_by_doc: dict[str, list[str]]


def _read_existing_concept_page(path: Path) -> _ExistingConceptPage:
    if not path.is_file():
        return _ExistingConceptPage(sections_by_doc={})
    text = path.read_text()
    parts = text.split(_CONCEPTS_MARKER)
    if len(parts) < 3:
        return _ExistingConceptPage(sections_by_doc={})
    try:
        raw = json.loads(parts[1].strip())
    except json.JSONDecodeError:
        return _ExistingConceptPage(sections_by_doc={})
    sections_by_doc = {
        str(doc_id): [str(section_id) for section_id in section_ids]
        for doc_id, section_ids in raw.items()
        if isinstance(section_ids, list)
    }
    return _ExistingConceptPage(sections_by_doc=sections_by_doc)


def _remove_stale_mentions(concepts_dir: Path, doc_id: str, current_slugs: set[str]) -> None:
    for path in concepts_dir.glob("*.md"):
        existing = _read_existing_concept_page(path)
        if doc_id not in existing.sections_by_doc:
            continue
        if path.stem in current_slugs:
            continue
        sections_by_doc = dict(existing.sections_by_doc)
        sections_by_doc.pop(doc_id, None)
        if not sections_by_doc:
            path.unlink()
            continue
        title = _title_from_existing_page(path)
        concept = ConceptEntry(slug=path.stem, title=title, section_ids=[])
        path.write_text(_render_concept(concept, sections_by_doc, concepts_dir))


def _title_from_existing_page(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("title: "):
            try:
                value = json.loads(line.removeprefix("title: "))
            except json.JSONDecodeError:
                break
            if isinstance(value, str):
                return value
    return path.stem.replace("-", " ").title()


def _read_compiled_sections_for_doc(concepts_dir: Path, doc_id: str) -> list[Section]:
    manifest = concepts_dir.parent / "books" / doc_id / "sections" / "sections.jsonl"
    if manifest.is_file():
        return read_sections(manifest)
    # Wiki section pages do not carry enough machine-readable text to round-trip
    # from Markdown, so fall back to the source sections manifest when the backend
    # is used in a normal BookGraph workspace.
    source_manifest = (
        concepts_dir.parent.parent / "sources" / "sections" / doc_id / "sections.jsonl"
    )
    return read_sections(source_manifest)


def _replace_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _doc_id(sections: list[Section]) -> str:
    if not sections:
        return "untitled"
    return sections[0].doc_id


def _wiki_link(concept: ConceptEntry) -> str:
    return f"[[{concept.slug}|{concept.title.replace('|', '/') }]]"


def _escape_markdown_link_text(value: str) -> str:
    return value.replace("[", r"\[").replace("]", r"\]")
