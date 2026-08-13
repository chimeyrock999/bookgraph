"""Deterministic, local concept extraction shared across the pipeline.

Both the ``markdown-graph`` wiki backend (for in-page ``[[slug|Title]]`` wikilinks)
and the index stage (for the ``concept_mentions`` table) extract concepts from the
same input — a document's ``sections.jsonl`` — using :func:`extract_concepts`, so a
section produces the same concept slugs/titles on both sides. Keeping this in one
module is what lets the cross-book concept graph (index) and the per-book wikilinks
(wiki) stay consistent without either reading the other's output.

Extraction is intentionally simple and deterministic: no LLMs, embeddings, or
external services — just high-signal repeated title-case / long domain terms drawn
from section titles, heading paths, and body text.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from bookgraph.models import Section
from bookgraph.utils import slugify

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
    """A concept extracted from one document, with the sections that mention it."""

    slug: str
    title: str
    section_ids: list[str]

    @property
    def section_count(self) -> int:
        return len(self.section_ids)


def extract_concepts(sections: list[Section], *, max_concepts: int = 50) -> list[ConceptEntry]:
    """Extract deterministic lightweight concepts from section titles/headings/text.

    This intentionally avoids LLMs/embeddings: concepts are high-signal repeated
    title-case or long domain terms. It is a first linked-wiki backend, not a
    semantic concept miner.
    """

    occurrences: dict[str, list[str]] = {}
    display_titles: dict[str, str] = {}
    counts: Counter[str] = Counter()

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

    # Key each concept by a *stable* slug derived only from its title, so the slug
    # is a content-derived identity usable as a cross-book join key in the index.
    # Distinct terms that slugify to the same value (e.g. the phrase "Schema
    # Evolution" and the token "schema-evolution") are merged into one concept
    # rather than disambiguated with a per-build ``-2`` counter, which is not stable
    # across documents/builds and would fragment or cross-contaminate the graph.
    by_slug: dict[str, ConceptEntry] = {}
    for key in ranked:
        title = display_titles[key]
        slug = slugify(title)
        existing = by_slug.get(slug)
        if existing is None:
            by_slug[slug] = ConceptEntry(
                slug=slug, title=title, section_ids=list(occurrences[key])
            )
            continue
        section_ids = list(existing.section_ids)
        for section_id in occurrences[key]:
            if section_id not in section_ids:
                section_ids.append(section_id)
        by_slug[slug] = ConceptEntry(
            slug=existing.slug, title=existing.title, section_ids=section_ids
        )
    return list(by_slug.values())


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
