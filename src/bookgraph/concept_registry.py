"""Curated concept registry: canonical concepts, aliases, ignores, and distinct pairs.

Agent annotations and the deterministic extractor can both produce near-duplicate or
overly granular concepts (``metadata-file`` vs ``table-metadata``). The registry is the
human-curated **source artifact** that canonicalizes them over time:

- a **canonical** concept has an authoritative title and a set of deprecated
  **aliases** that resolve to it;
- an **ignored** slug is never turned into a concept edge (a global prune for generic
  noun phrases the extractor keeps surfacing);
- a **distinct** pair records that two similar-looking concepts were reviewed and are
  *not* duplicates, so merge suggestions stop proposing them.

It lives at ``concepts/registry.json`` alongside the other source artifacts and is
read — never written — by ``index build``, which rewrites alias edges to their canonical
slug while keeping the original slug as provenance. Only the ``bookgraph concepts``
CLI writes it. See ``docs/cli/concepts.md`` for the contract.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from bookgraph.annotations import ConceptEdge
from bookgraph.utils import validate_slug_id


class CanonicalConcept(BaseModel):
    """A reviewed, canonical concept and the deprecated aliases that resolve to it."""

    slug: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    note: str = ""


class ConceptRegistry(BaseModel):
    """The workspace's concept registry (``concepts/registry.json``).

    Invariants (validated on load and after every mutation): every slug is a
    filesystem-safe id; an alias belongs to exactly one canonical concept and is never
    itself canonical (no alias chains); ignored slugs are neither canonical nor aliases.
    """

    canonical: list[CanonicalConcept] = Field(default_factory=list)
    ignored: list[str] = Field(default_factory=list)
    distinct: list[tuple[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_invariants(self) -> ConceptRegistry:
        canonical_slugs: set[str] = set()
        for record in self.canonical:
            validate_slug_id(record.slug, field_name="concept slug")
            if record.slug in canonical_slugs:
                raise ValueError(f"concept '{record.slug}' is listed as canonical twice")
            canonical_slugs.add(record.slug)
        alias_owner: dict[str, str] = {}
        for record in self.canonical:
            for alias in record.aliases:
                validate_slug_id(alias, field_name="alias slug")
                if alias in canonical_slugs:
                    raise ValueError(
                        f"alias '{alias}' of '{record.slug}' is itself a canonical concept"
                    )
                owner = alias_owner.setdefault(alias, record.slug)
                if owner != record.slug:
                    raise ValueError(
                        f"alias '{alias}' resolves to both '{owner}' and '{record.slug}'"
                    )
        for slug in self.ignored:
            validate_slug_id(slug, field_name="ignored slug")
            if slug in canonical_slugs or slug in alias_owner:
                raise ValueError(f"ignored slug '{slug}' is also canonical or an alias")
        for left, right in self.distinct:
            validate_slug_id(left, field_name="distinct slug")
            validate_slug_id(right, field_name="distinct slug")
        return self

    # -- lookups -------------------------------------------------------------------

    def record(self, slug: str) -> CanonicalConcept | None:
        """The canonical record for ``slug`` (itself canonical, not an alias)."""

        return next((record for record in self.canonical if record.slug == slug), None)

    def canonical_of(self, slug: str) -> str | None:
        """The canonical slug an alias resolves to, or ``None`` if ``slug`` is no alias."""

        for record in self.canonical:
            if slug in record.aliases:
                return record.slug
        return None

    def resolve(self, slug: str) -> str:
        """``slug``'s canonical slug: an alias resolves to its owner, else itself."""

        return self.canonical_of(slug) or slug

    def is_ignored(self, slug: str) -> bool:
        return slug in self.ignored

    def is_known(self, slug: str) -> bool:
        """Whether a reviewer has already decided about ``slug`` (canonical/alias/ignored)."""

        return (
            self.record(slug) is not None
            or self.canonical_of(slug) is not None
            or self.is_ignored(slug)
        )

    def is_distinct(self, left: str, right: str) -> bool:
        pair = tuple(sorted((left, right)))
        return any(tuple(sorted(item)) == pair for item in self.distinct)


def read_registry(path: Path) -> ConceptRegistry:
    """Load the registry; a missing file is an empty registry.

    A present-but-invalid file raises ``ValueError`` rather than degrading to empty:
    silently dropping every alias would un-merge the graph on the next build.
    """

    if not path.is_file():
        return ConceptRegistry()
    try:
        return ConceptRegistry.model_validate_json(path.read_text())
    except ValueError as exc:
        raise ValueError(f"Invalid concept registry {path}: {exc}") from exc


def write_registry(registry: ConceptRegistry, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(registry.model_dump_json(indent=2) + "\n")
    return path


def title_from_slug(slug: str) -> str:
    """A readable fallback title for a slug (``table-metadata`` → ``Table Metadata``)."""

    return " ".join(word.capitalize() for word in slug.split("-"))


# -- mutations (each returns a new, re-validated registry) ---------------------------


def _rebuild(registry: ConceptRegistry, **update: object) -> ConceptRegistry:
    payload = registry.model_dump()
    payload.update(update)
    return ConceptRegistry.model_validate(payload)


def _without(slug: str, records: list[CanonicalConcept]) -> list[CanonicalConcept]:
    """Drop ``slug`` from every record's aliases (it is about to change role)."""

    return [
        record.model_copy(update={"aliases": [a for a in record.aliases if a != slug]})
        for record in records
    ]


def set_canonical(
    registry: ConceptRegistry, slug: str, title: str, note: str | None = None
) -> ConceptRegistry:
    """Mark ``slug`` canonical with ``title`` (accepting a review-queue concept).

    A slug that was an alias or ignored is promoted: it leaves those roles.
    """

    validate_slug_id(slug, field_name="concept slug")
    records = _without(slug, registry.canonical)
    existing = next((r for r in records if r.slug == slug), None)
    if existing is None:
        records.append(CanonicalConcept(slug=slug, title=title, note=note or ""))
    else:
        update: dict[str, object] = {"title": title}
        if note is not None:
            update["note"] = note
        records = [r.model_copy(update=update) if r.slug == slug else r for r in records]
    ignored = [s for s in registry.ignored if s != slug]
    return _rebuild(registry, canonical=[r.model_dump() for r in records], ignored=ignored)


def add_alias(registry: ConceptRegistry, alias: str, canonical: str, title: str) -> ConceptRegistry:
    """Make ``alias`` resolve to ``canonical`` (merging the two concepts).

    ``canonical`` is created with ``title`` if it is not yet in the registry (promoting
    it out of ``ignored`` if needed). If ``alias`` was itself canonical, its own aliases
    move to ``canonical`` too, so a merge never leaves an alias chain. ``canonical``
    may not be an alias itself. A ``distinct`` verdict between the merged slugs is
    removed.
    """

    validate_slug_id(alias, field_name="alias slug")
    validate_slug_id(canonical, field_name="concept slug")
    if alias == canonical:
        raise ValueError("a concept cannot be an alias of itself")
    owner = registry.canonical_of(canonical)
    if owner is not None:
        raise ValueError(
            f"'{canonical}' is an alias of '{owner}'; alias '{alias}' to '{owner}' instead"
        )

    merged = registry.record(alias)
    moved = [alias, *(merged.aliases if merged is not None else [])]
    # Merging supersedes earlier decisions about the pair: a canonical that was ignored
    # is promoted (as set_canonical does), and a "distinct" verdict between the merged
    # slugs is dropped so it cannot resurface after a later unalias.
    distinct = [
        pair
        for pair in registry.distinct
        if not (canonical in pair and any(slug in pair for slug in moved))
    ]
    records = [r for r in _without(alias, registry.canonical) if r.slug != alias]
    if not any(r.slug == canonical for r in records):
        records.append(CanonicalConcept(slug=canonical, title=title))
    records = [
        r.model_copy(update={"aliases": [*r.aliases, *(a for a in moved if a not in r.aliases)]})
        if r.slug == canonical
        else r
        for r in records
    ]
    ignored = [s for s in registry.ignored if s not in (alias, canonical)]
    return _rebuild(
        registry,
        canonical=[r.model_dump() for r in records],
        ignored=ignored,
        distinct=distinct,
    )


def remove_alias(registry: ConceptRegistry, alias: str) -> ConceptRegistry:
    """Detach ``alias`` from its canonical concept (it becomes a concept of its own)."""

    if registry.canonical_of(alias) is None:
        raise ValueError(f"'{alias}' is not an alias")
    records = _without(alias, registry.canonical)
    return _rebuild(registry, canonical=[r.model_dump() for r in records])


def ignore(registry: ConceptRegistry, slug: str) -> ConceptRegistry:
    """Never turn ``slug`` into a concept edge again (a global prune)."""

    validate_slug_id(slug, field_name="ignored slug")
    if registry.record(slug) is not None:
        raise ValueError(f"'{slug}' is a canonical concept; it cannot be ignored")
    records = _without(slug, registry.canonical)
    ignored = registry.ignored if slug in registry.ignored else [*registry.ignored, slug]
    return _rebuild(registry, canonical=[r.model_dump() for r in records], ignored=ignored)


def mark_distinct(registry: ConceptRegistry, left: str, right: str) -> ConceptRegistry:
    """Record that ``left`` and ``right`` are reviewed as different concepts."""

    validate_slug_id(left, field_name="distinct slug")
    validate_slug_id(right, field_name="distinct slug")
    if left == right:
        raise ValueError("a concept cannot be distinct from itself")
    if registry.is_distinct(left, right):
        return registry
    pair = tuple(sorted((left, right)))
    return _rebuild(registry, distinct=[*registry.distinct, pair])


# -- build-time canonicalization ------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def canonicalize_edges(edges: list[ConceptEdge], registry: ConceptRegistry) -> list[ConceptEdge]:
    """Apply the registry to merged concept edges before they are stored.

    Ignored slugs are dropped; an alias edge is rewritten to its canonical slug, keeping
    the original slug in ``raw_slug`` as provenance; a canonical concept's edges take
    the registry title so every mention aggregates under one display title.

    Two edges in one section can collapse onto one canonical slug (the agent asserted
    both ``table-metadata`` and its alias ``metadata-file``). They become one mention,
    and the mention keeps **one edge whole** — slug provenance and gloss together, never
    mixed: the edge asserted directly under the canonical slug wins, else the first
    alias edge. The ``raw_slug`` of any further colliding alias is not stored (the
    mention has one provenance slot); it remains in the annotation artifact.
    """

    result: list[ConceptEdge] = []
    index: dict[tuple[str, str], int] = {}
    for edge in edges:
        if registry.is_ignored(edge.slug):
            continue
        slug = registry.resolve(edge.slug)
        record = registry.record(slug)
        title = record.title if record is not None else edge.title
        raw_slug = edge.slug if slug != edge.slug else edge.raw_slug
        canonicalized = ConceptEdge(
            slug=slug,
            title=_WHITESPACE_RE.sub(" ", title).strip() or slug,
            section_id=edge.section_id,
            gloss=edge.gloss,
            source=edge.source,
            raw_slug=raw_slug,
        )
        key = (edge.section_id, slug)
        if key in index:
            if result[index[key]].raw_slug and not raw_slug:
                result[index[key]] = canonicalized  # the direct assertion beats an alias
            continue
        index[key] = len(result)
        result.append(canonicalized)
    return result
