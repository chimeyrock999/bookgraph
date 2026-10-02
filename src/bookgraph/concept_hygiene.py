"""Concept hygiene over the built concept graph: merge suggestions, lint, review queue.

Pure, deterministic functions over the index's concept outputs plus the curated
:class:`~bookgraph.concept_registry.ConceptRegistry`. They never write anything; a
reviewer acts on them through the ``bookgraph concepts`` CLI (alias / canonical /
ignore / distinct), which edits the registry that the next ``index build`` applies.

- :func:`suggest_merges` proposes likely-duplicate concept pairs (inflection variants,
  acronyms, one slug subsuming another, near-identical spelling) with a suggested
  canonical side.
- :func:`lint_concepts` flags concepts that should probably not be durable: generic
  one-word terms, one-off auto mentions, over-granular phrases, and stale aliases that
  survive only because the index has not been rebuilt.
- :func:`review_queue` lists agent-created concepts no reviewer has decided on yet.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, Field

from bookgraph.concept_registry import ConceptRegistry
from bookgraph.index.base import Concept, ConceptNode

DEFAULT_MERGE_THRESHOLD = 0.5
_SPELLING_THRESHOLD = 0.88
_GRANULAR_TOKENS = 4

# Words that are almost never a concept on their own. The Tier-1 extractor keeps any
# >=6-char token, so these (and short structural words from headings) are its most
# common false positives.
GENERIC_TERMS = frozenset(
    {
        "about",
        "according",
        "additional",
        "also",
        "another",
        "appendix",
        "approach",
        "available",
        "based",
        "because",
        "before",
        "between",
        "case",
        "cases",
        "chapter",
        "common",
        "conclusion",
        "contents",
        "could",
        "data",
        "different",
        "example",
        "examples",
        "figure",
        "first",
        "following",
        "general",
        "however",
        "important",
        "information",
        "introduction",
        "several",
        "many",
        "more",
        "new",
        "note",
        "notes",
        "number",
        "other",
        "overview",
        "page",
        "part",
        "preface",
        "problem",
        "problems",
        "process",
        "result",
        "results",
        "second",
        "section",
        "should",
        "simple",
        "since",
        "summary",
        "system",
        "systems",
        "therefore",
        "these",
        "third",
        "those",
        "through",
        "together",
        "using",
        "value",
        "values",
        "various",
        "where",
        "which",
        "within",
        "without",
        "would",
    }
)

Severity = Literal["warning", "info"]


class MergeSuggestion(BaseModel):
    """Two concepts that look like duplicates, with the side suggested as canonical."""

    canonical: str
    alias: str
    canonical_title: str
    alias_title: str
    score: float
    reason: str


class LintFinding(BaseModel):
    """One hygiene problem with a concept. ``warning`` findings make it non-durable."""

    slug: str
    title: str
    rule: str
    severity: Severity
    message: str


class ReviewItem(BaseModel):
    """An agent-created concept awaiting a reviewer's decision."""

    slug: str
    title: str
    doc_count: int
    mention_count: int
    gloss: str = ""
    sections: list[str] = Field(default_factory=list)


# -- merge suggestions ----------------------------------------------------------------


def _stem(token: str) -> str:
    """A tiny plural folder: ``snapshots``→``snapshot``, ``indexes``→``index``."""

    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("ses", "xes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _stems(slug: str) -> list[str]:
    return [_stem(token) for token in slug.split("-") if token]


def _similarity(left: str, right: str) -> tuple[float, str] | None:
    """Score how likely two slugs name the same concept, with the reason, or ``None``."""

    left_stems, right_stems = _stems(left), _stems(right)
    if left_stems == right_stems or (
        len(left_stems) > 1 and sorted(left_stems) == sorted(right_stems)
    ):
        return 1.0, "inflection or word-order variant"

    short, long = sorted((left_stems, right_stems), key=len)
    if len(short) == 1 and len(long) >= 2 and "".join(t[0] for t in long) == short[0]:
        return 0.9, "acronym"

    best: tuple[float, str] | None = None
    if short and set(short) < set(long):
        # A lone word inside a phrase ("table" in "table-metadata") is weak evidence —
        # it is usually the phrase's head noun, not a duplicate — so it scores lower.
        weight = 1.0 if len(short) > 1 else 0.8
        best = (weight * len(short) / len(long), "one concept's words subsume the other's")

    ratio = SequenceMatcher(None, left, right)
    if ratio.real_quick_ratio() >= _SPELLING_THRESHOLD and ratio.ratio() >= _SPELLING_THRESHOLD:
        spelling = round(ratio.ratio(), 3)
        if best is None or spelling > best[0]:
            best = (spelling, "near-identical spelling")
    return best


def _canonical_rank(node: ConceptNode, registry: ConceptRegistry) -> tuple[int, int, int, int, str]:
    # Lower sorts first: registry-canonical, then most books, most mentions, fewest words.
    return (
        0 if registry.record(node.slug) is not None else 1,
        -node.doc_count,
        -node.mention_count,
        len(node.slug.split("-")),
        node.slug,
    )


def suggest_merges(
    nodes: list[ConceptNode],
    registry: ConceptRegistry,
    *,
    threshold: float = DEFAULT_MERGE_THRESHOLD,
    limit: int | None = None,
) -> list[MergeSuggestion]:
    """Likely-duplicate concept pairs, best first.

    Pairs a reviewer already decided on are skipped: ignored slugs, pairs marked
    distinct, and aliases (which are already merged). Each suggestion names the side
    that should become canonical — a registry-canonical concept, else the better
    connected one.
    """

    candidates = [
        node
        for node in nodes
        if not registry.is_ignored(node.slug) and registry.canonical_of(node.slug) is None
    ]
    suggestions: list[MergeSuggestion] = []
    for i, left in enumerate(candidates):
        for right in candidates[i + 1 :]:
            if registry.is_distinct(left.slug, right.slug):
                continue
            scored = _similarity(left.slug, right.slug)
            if scored is None or scored[0] < threshold:
                continue
            canonical, alias = sorted((left, right), key=lambda n: _canonical_rank(n, registry))
            suggestions.append(
                MergeSuggestion(
                    canonical=canonical.slug,
                    alias=alias.slug,
                    canonical_title=canonical.title,
                    alias_title=alias.title,
                    score=round(scored[0], 3),
                    reason=scored[1],
                )
            )
    suggestions.sort(key=lambda s: (-s.score, s.canonical, s.alias))
    return suggestions if limit is None else suggestions[:limit]


# -- lint -------------------------------------------------------------------------------


def _is_generic(slug: str) -> bool:
    tokens = slug.split("-")
    return all(token in GENERIC_TERMS or _stem(token) in GENERIC_TERMS for token in tokens)


def lint_concepts(concepts: list[Concept], registry: ConceptRegistry) -> list[LintFinding]:
    """Hygiene findings for every concept, in input order.

    Registry-canonical concepts are exempt from the noise rules (a reviewer vouched for
    them); the stale-alias/ignored rule applies to every concept.
    """

    return [
        LintFinding(
            slug=concept.node.slug,
            title=concept.node.title,
            rule=rule,
            severity=severity,
            message=message,
        )
        for concept in concepts
        for rule, severity, message in _lint_one(concept, registry)
    ]


def _lint_one(concept: Concept, registry: ConceptRegistry) -> list[tuple[str, Severity, str]]:
    node = concept.node
    owner = registry.canonical_of(node.slug)
    if owner is not None:
        return [
            (
                "stale-alias",
                "warning",
                f"'{node.slug}' is an alias of '{owner}'; run 'bookgraph index build' to "
                "fold its mentions into the canonical concept",
            )
        ]
    if registry.is_ignored(node.slug):
        return [
            (
                "stale-ignored",
                "warning",
                f"'{node.slug}' is ignored; run 'bookgraph index build' to drop its mentions",
            )
        ]
    if registry.record(node.slug) is not None:
        return []

    found: list[tuple[str, Severity, str]] = []
    if _is_generic(node.slug):
        found.append(
            (
                "generic-term",
                "warning",
                "a generic word rather than a domain concept; ignore it or replace it with "
                "a specific concept",
            )
        )
    if node.mention_count == 1 and all(m.source == "auto" for m in concept.mentions):
        found.append(
            (
                "one-off",
                "warning",
                "an auto-extracted term mentioned in a single section; likely incidental",
            )
        )
    words = len(node.slug.split("-"))
    if words >= _GRANULAR_TOKENS:
        found.append(
            (
                "over-granular",
                "info",
                f"{words}-word phrase; consider aliasing it to a broader concept",
            )
        )
    return found


def durable_slugs(concepts: list[Concept], registry: ConceptRegistry) -> set[str]:
    """Slugs worth a durable concept page: no ``warning`` lint finding."""

    flagged = {
        finding.slug
        for finding in lint_concepts(concepts, registry)
        if finding.severity == "warning"
    }
    return {concept.node.slug for concept in concepts if concept.node.slug not in flagged}


# -- review queue -------------------------------------------------------------------------


def review_queue(concepts: list[Concept], registry: ConceptRegistry) -> list[ReviewItem]:
    """Agent-created concepts that are not yet canonical, aliased, or ignored."""

    items: list[ReviewItem] = []
    for concept in concepts:
        agent_mentions = [m for m in concept.mentions if m.source == "agent"]
        if not agent_mentions or registry.is_known(concept.node.slug):
            continue
        items.append(
            ReviewItem(
                slug=concept.node.slug,
                title=concept.node.title,
                doc_count=concept.node.doc_count,
                mention_count=concept.node.mention_count,
                gloss=next((m.gloss for m in agent_mentions if m.gloss), ""),
                sections=[m.section_id for m in agent_mentions],
            )
        )
    return items
