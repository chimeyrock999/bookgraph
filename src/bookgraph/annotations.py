"""Tier-2 agent annotations: the source of truth + the Tier-1/Tier-2 merge.

A reading agent that reads each section to explain it can identify the section's
*real* concepts far better than the deterministic Tier-1 tokenizer. The MCP
``annotate_section`` tool records that judgment as a per-section source artifact
(``annotations/<doc_id>/<section_id>.json``); this module owns building, persisting,
reading, and — at ``index build`` time — merging those annotations with the
deterministic baseline.

The merge is **presence-based**: a section that has an annotation takes its concept
edges from the annotation (``source="agent"``, carrying gloss) *even when the
annotation's concept list is empty* — the empty list is the tokenizer-false-positive
prune. A section with no annotation keeps the auto extraction (``source="auto"``). See
``docs/cli/annotations.md`` for the full contract.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from bookgraph.concepts import extract_concepts
from bookgraph.models import AnnotatedConcept, Section, SectionAnnotation
from bookgraph.utils import slugify, validate_slug_id

_WHITESPACE_RE = re.compile(r"\s+")


def _clean(text: str) -> str:
    """Collapse all whitespace (incl. newlines) to single spaces and strip.

    Gloss/summary/title are LLM-generated free text that gets spliced into single-line
    Markdown (concept-page bullets, link text). Normalising to one line at write time
    means every downstream consumer gets a value that cannot break the surrounding
    Markdown with a stray newline.
    """

    return _WHITESPACE_RE.sub(" ", text).strip()


def _resolve_concept(concept: AnnotatedConcept) -> AnnotatedConcept:
    """Normalise one agent concept: derive/clean its slug, reject an empty one.

    A caller may omit the slug (deriving it from the title) or pass a display-style
    one; either way it is slugified to the cross-book join form. ``slugify`` returns
    the ``untitled`` sentinel for text with no usable characters — that (and an empty
    slug) is rejected so a junk concept never lands in the graph.
    """

    slug = slugify(concept.slug or concept.title)
    if not slug or slug == "untitled":
        raise ValueError(
            "each annotated concept needs a title (or slug) that slugifies to a "
            f"non-empty, non-'untitled' value; got title={concept.title!r} "
            f"slug={concept.slug!r}"
        )
    title = _clean(concept.title) or slug
    return AnnotatedConcept(slug=slug, title=title, gloss=_clean(concept.gloss))


def build_annotation(
    doc_id: str,
    section_id: str,
    concepts: Iterable[AnnotatedConcept] | None,
    *,
    summary: str = "",
    model: str | None = None,
    created_at: str | None = None,
) -> SectionAnnotation:
    """Build a validated :class:`SectionAnnotation` from raw agent input.

    ``doc_id`` is validated as a filesystem-safe slug (it names the artifact folder);
    ``section_id`` is not (it has the ``<doc_id>.<slug>`` dotted form) — the caller
    validates it by membership against the document's sections.

    ``concepts`` distinguishes three intents (preserved through to the merge):
    ``None`` = no opinion (keep Tier-1), ``[]`` = deliberate prune, a list = replace.
    When a list is given, concepts are deduplicated by slug: the first occurrence wins
    its title and the first non-empty gloss for a slug wins. Free-text gloss/summary/
    title are normalised to a single line so they cannot corrupt rendered Markdown.
    """

    validate_slug_id(doc_id, field_name="doc_id")

    resolved_concepts: list[AnnotatedConcept] | None
    if concepts is None:
        resolved_concepts = None
    else:
        by_slug: dict[str, AnnotatedConcept] = {}
        for raw in concepts:
            resolved = _resolve_concept(raw)
            existing = by_slug.get(resolved.slug)
            if existing is None:
                by_slug[resolved.slug] = resolved
            elif not existing.gloss and resolved.gloss:
                by_slug[resolved.slug] = existing.model_copy(update={"gloss": resolved.gloss})
        resolved_concepts = list(by_slug.values())

    return SectionAnnotation(
        doc_id=doc_id,
        section_id=section_id,
        concepts=resolved_concepts,
        summary=_clean(summary),
        model=model,
        created_at=created_at,
    )


def annotation_path(annotations_root: Path, doc_id: str, section_id: str) -> Path:
    """Canonical location of one section's annotation file."""

    return annotations_root / doc_id / f"{section_id}.json"


def write_annotation(annotation: SectionAnnotation, path: Path) -> Path:
    """Persist an annotation to ``annotations/<doc_id>/<section_id>.json``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(annotation.model_dump_json(indent=2) + "\n")
    return path


def read_annotation(path: Path) -> SectionAnnotation:
    """Load an annotation written by :func:`write_annotation`."""

    return SectionAnnotation.model_validate_json(path.read_text())


def read_annotations_for_doc(annotations_root: Path, doc_id: str) -> dict[str, SectionAnnotation]:
    """Every readable annotation for a document, keyed by ``section_id``.

    Unreadable/corrupt annotation files are skipped rather than failing the whole
    merge (an annotation is advisory enrichment, never load-bearing for the build).

    An annotation's *location* on disk is not trusted: a payload whose ``doc_id`` does
    not match the requested document, or whose ``section_id`` does not match its own
    filename, is treated as misplaced and skipped — so a stale/misplaced file can never
    override or prune another document's (or another section's) Tier-1 concepts.
    """

    doc_root = annotations_root / doc_id
    annotations: dict[str, SectionAnnotation] = {}
    for path in sorted(doc_root.glob("*.json")) if doc_root.is_dir() else []:
        try:
            annotation = read_annotation(path)
        except (OSError, ValueError):
            continue
        if annotation.doc_id != doc_id or annotation.section_id != path.stem:
            continue
        annotations[annotation.section_id] = annotation
    return annotations


@dataclass(frozen=True)
class ConceptEdge:
    """One concept→section edge to persist: which tier it came from + its gloss."""

    slug: str
    title: str
    section_id: str
    gloss: str
    source: str


def _has_agent_concepts(annotation: SectionAnnotation | None) -> bool:
    """True when the agent gave an explicit concept opinion (a list, incl. ``[]``)."""

    return annotation is not None and annotation.concepts is not None


def merge_section_concepts(
    sections: list[Section], annotations: dict[str, SectionAnnotation]
) -> list[ConceptEdge]:
    """Merge Tier-1 (auto) and Tier-2 (agent) concept edges.

    Per section, the edges come from the agent when it gave an explicit concept
    opinion (``concepts`` is a list — a non-empty list replaces Tier-1, and ``[]`` is
    the deliberate prune, yielding no edges), and from the deterministic extractor
    otherwise (no annotation, or a summary-only annotation whose ``concepts`` is
    ``None``). Sections the agent has taken over are excluded from ``extract_concepts``
    entirely, so their discarded auto terms never consume slots in its global
    ``max_concepts`` ranking at the expense of a real term in an unannotated section.
    """

    agent_sections = {
        section_id for section_id, ann in annotations.items() if _has_agent_concepts(ann)
    }
    auto_input = [section for section in sections if section.id not in agent_sections]

    auto_by_section: dict[str, list[tuple[str, str]]] = {}
    for entry in extract_concepts(auto_input):
        for section_id in entry.section_ids:
            auto_by_section.setdefault(section_id, []).append((entry.slug, entry.title))

    edges: list[ConceptEdge] = []
    for section in sections:
        annotation = annotations.get(section.id)
        if _has_agent_concepts(annotation):
            assert annotation is not None and annotation.concepts is not None
            for concept in annotation.concepts:
                edges.append(
                    ConceptEdge(
                        slug=concept.slug,
                        title=concept.title,
                        section_id=section.id,
                        gloss=concept.gloss,
                        source="agent",
                    )
                )
        else:
            for slug, title in auto_by_section.get(section.id, []):
                edges.append(
                    ConceptEdge(
                        slug=slug,
                        title=title,
                        section_id=section.id,
                        gloss="",
                        source="auto",
                    )
                )
    return edges
