from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bookgraph.models import Section
from bookgraph.ports import WikiBackend
from bookgraph.sections import render_section_markdown
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


@dataclass(frozen=True)
class SectionMention:
    id: str
    doc_id: str
    title: str


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

        doc_id = _doc_id(sections)
        concepts = extract_concepts(sections)
        current_slugs = {concept.slug for concept in concepts}
        concept_map = {concept.slug: concept for concept in concepts}
        concepts_by_section = _concepts_by_section(concepts)
        current_mentions = _mentions_by_section_id(sections)

        for section in sections:
            section_concepts = [
                concept_map[slug] for slug in concepts_by_section.get(section.id, [])
            ]
            (sections_dir / f"{section.id}.md").write_text(
                _render_section(section, section_concepts)
            )

        _remove_stale_mentions(concepts_dir, doc_id, current_slugs)
        for concept in concepts:
            concept_path = concepts_dir / f"{concept.slug}.md"
            existing = _read_existing_concept_page(concept_path)
            mentions_by_doc = dict(existing.mentions_by_doc)
            mentions_by_doc[doc_id] = [
                current_mentions[section_id] for section_id in concept.section_ids
            ]
            concept_title = existing.title or concept.title
            (concepts_dir / f"{concept.slug}.md").write_text(
                _render_concept(
                    ConceptEntry(
                        slug=concept.slug,
                        title=concept_title,
                        section_ids=concept.section_ids,
                    ),
                    mentions_by_doc,
                )
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
    mentions_by_doc: dict[str, list[SectionMention]],
) -> str:
    section_count = sum(len(mentions) for mentions in mentions_by_doc.values())
    lines = [
        "---",
        f"concept: {json.dumps(concept.slug)}",
        f"title: {json.dumps(concept.title)}",
        f"section_count: {section_count}",
        "---",
        "",
        f"# {concept.title}",
        "",
        _CONCEPTS_MARKER,
        json.dumps(_mentions_to_json(mentions_by_doc), sort_keys=True),
        _CONCEPTS_MARKER,
        "",
        "## Mentioned in",
        "",
    ]
    for doc_id in sorted(mentions_by_doc):
        for mention in mentions_by_doc[doc_id]:
            title = _escape_markdown_link_text(mention.title)
            lines.append(f"- [{title}](../books/{doc_id}/sections/{mention.id}.md)")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class _ExistingConceptPage:
    title: str | None
    mentions_by_doc: dict[str, list[SectionMention]]


def _read_existing_concept_page(path: Path) -> _ExistingConceptPage:
    if not path.is_file():
        return _ExistingConceptPage(title=None, mentions_by_doc={})
    text = path.read_text()
    parts = text.split(_CONCEPTS_MARKER)
    if len(parts) < 3:
        return _ExistingConceptPage(title=_title_from_text(text), mentions_by_doc={})
    try:
        raw = json.loads(parts[1].strip())
    except json.JSONDecodeError:
        return _ExistingConceptPage(title=_title_from_text(text), mentions_by_doc={})
    mentions_by_doc = _mentions_from_json(raw)
    return _ExistingConceptPage(title=_title_from_text(text), mentions_by_doc=mentions_by_doc)


def _remove_stale_mentions(concepts_dir: Path, doc_id: str, current_slugs: set[str]) -> None:
    for path in concepts_dir.glob("*.md"):
        existing = _read_existing_concept_page(path)
        if doc_id not in existing.mentions_by_doc:
            continue
        if path.stem in current_slugs:
            continue
        mentions_by_doc = dict(existing.mentions_by_doc)
        mentions_by_doc.pop(doc_id, None)
        if not mentions_by_doc:
            path.unlink()
            continue
        title = existing.title or path.stem.replace("-", " ").title()
        concept = ConceptEntry(slug=path.stem, title=title, section_ids=[])
        path.write_text(_render_concept(concept, mentions_by_doc))


def _mentions_by_section_id(sections: list[Section]) -> dict[str, SectionMention]:
    return {
        section.id: SectionMention(id=section.id, doc_id=section.doc_id, title=section.title)
        for section in sections
    }


def _mentions_to_json(
    mentions_by_doc: dict[str, list[SectionMention]],
) -> dict[str, list[dict[str, str]]]:
    return {
        doc_id: [
            {
                "id": mention.id,
                "title": mention.title,
            }
            for mention in mentions
        ]
        for doc_id, mentions in mentions_by_doc.items()
    }


def _mentions_from_json(raw: object) -> dict[str, list[SectionMention]]:
    if not isinstance(raw, dict):
        return {}
    mentions_by_doc: dict[str, list[SectionMention]] = {}
    for doc_id, raw_mentions in raw.items():
        if not isinstance(doc_id, str) or not isinstance(raw_mentions, list):
            continue
        mentions = [_parse_mention(doc_id, item) for item in raw_mentions]
        mentions_by_doc[doc_id] = [mention for mention in mentions if mention is not None]
    return mentions_by_doc


def _parse_mention(doc_id: str, raw: object) -> SectionMention | None:
    if isinstance(raw, str):
        # Backward-compatible read of the first PR iteration's hidden state.
        return SectionMention(id=raw, doc_id=doc_id, title=raw)
    if not isinstance(raw, dict):
        return None
    section_id = raw.get("id")
    title = raw.get("title")
    if not isinstance(section_id, str) or not isinstance(title, str):
        return None
    return SectionMention(id=section_id, doc_id=doc_id, title=title)


def _title_from_text(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("title: "):
            try:
                value = json.loads(line.removeprefix("title: "))
            except json.JSONDecodeError:
                break
            if isinstance(value, str):
                return value
    return None


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
