from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.annotations import ConceptEdge
from bookgraph.concept_registry import (
    CanonicalConcept,
    ConceptRegistry,
    add_alias,
    canonicalize_edges,
    ignore,
    mark_distinct,
    read_registry,
    remove_alias,
    set_canonical,
    title_from_slug,
    write_registry,
)


def _edge(slug: str, section_id: str = "s.a", *, title: str = "", gloss: str = "") -> ConceptEdge:
    return ConceptEdge(
        slug=slug,
        title=title or title_from_slug(slug),
        section_id=section_id,
        gloss=gloss,
        source="agent",
    )


def test_alias_resolves_to_canonical() -> None:
    registry = add_alias(ConceptRegistry(), "metadata-file", "table-metadata", "Table Metadata")

    assert registry.resolve("metadata-file") == "table-metadata"
    assert registry.resolve("table-metadata") == "table-metadata"
    assert registry.resolve("unrelated") == "unrelated"
    record = registry.record("table-metadata")
    assert record is not None
    assert record.title == "Table Metadata"
    assert record.aliases == ["metadata-file"]


def test_aliasing_a_canonical_moves_its_aliases_so_no_chain_forms() -> None:
    registry = add_alias(ConceptRegistry(), "catalog-pointer", "metadata-file", "Metadata File")
    registry = add_alias(registry, "metadata-file", "table-metadata", "Table Metadata")

    assert registry.record("metadata-file") is None
    assert registry.resolve("catalog-pointer") == "table-metadata"
    record = registry.record("table-metadata")
    assert record is not None
    assert sorted(record.aliases) == ["catalog-pointer", "metadata-file"]


def test_alias_target_may_not_be_an_alias() -> None:
    registry = add_alias(ConceptRegistry(), "metadata-file", "table-metadata", "Table Metadata")

    with pytest.raises(ValueError, match="alias of 'table-metadata'"):
        add_alias(registry, "catalog-pointer", "metadata-file", "Metadata File")


def test_invariants_reject_conflicting_aliases() -> None:
    with pytest.raises(ValueError, match="resolves to both"):
        ConceptRegistry(
            canonical=[
                CanonicalConcept(slug="a", title="A", aliases=["x"]),
                CanonicalConcept(slug="b", title="B", aliases=["x"]),
            ]
        )
    with pytest.raises(ValueError):
        ConceptRegistry(canonical=[CanonicalConcept(slug="../bad", title="Bad")])


def test_canonical_ignore_and_unalias_change_roles() -> None:
    registry = add_alias(ConceptRegistry(), "snapshots", "snapshot", "Snapshot")
    registry = remove_alias(registry, "snapshots")
    assert registry.canonical_of("snapshots") is None

    registry = ignore(registry, "however")
    assert registry.is_ignored("however")
    registry = set_canonical(registry, "however", "However")  # promotion leaves ignored
    assert not registry.is_ignored("however")
    with pytest.raises(ValueError, match="canonical concept"):
        ignore(registry, "however")


def test_distinct_pairs_are_order_insensitive() -> None:
    registry = mark_distinct(ConceptRegistry(), "snapshot", "snapshot-id")

    assert registry.is_distinct("snapshot-id", "snapshot")
    assert mark_distinct(registry, "snapshot-id", "snapshot").distinct == registry.distinct


def test_registry_round_trips_and_missing_file_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "concepts" / "registry.json"
    assert read_registry(path) == ConceptRegistry()

    registry = mark_distinct(
        add_alias(ConceptRegistry(), "metadata-file", "table-metadata", "Table Metadata"),
        "snapshot",
        "snapshot-id",
    )
    write_registry(registry, path)

    assert read_registry(path) == registry


def test_invalid_registry_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text('{"canonical": [{"slug": "a", "title": "A", "aliases": ["a"]}]}')

    with pytest.raises(ValueError, match="Invalid concept registry"):
        read_registry(path)


def test_canonicalize_edges_rewrites_aliases_and_keeps_provenance() -> None:
    registry = add_alias(ConceptRegistry(), "metadata-file", "table-metadata", "Table Metadata")
    registry = ignore(registry, "however")

    edges = canonicalize_edges(
        [
            _edge("metadata-file", title="Metadata File"),
            _edge("table-metadata", title="table metadata", gloss="the root json"),
            _edge("however"),
            _edge("snapshot"),
        ],
        registry,
    )

    assert [(e.slug, e.title, e.raw_slug, e.gloss) for e in edges] == [
        # The edge asserted directly under the canonical slug wins the collision whole —
        # its gloss is never attached to the alias's raw slug.
        ("table-metadata", "Table Metadata", "", "the root json"),
        ("snapshot", "Snapshot", "", ""),
    ]


def test_canonicalize_edges_keeps_the_first_alias_edge_whole() -> None:
    registry = add_alias(ConceptRegistry(), "metadata-file", "table-metadata", "Table Metadata")
    registry = add_alias(registry, "catalog-pointer", "table-metadata", "Table Metadata")

    edges = canonicalize_edges(
        [_edge("metadata-file"), _edge("catalog-pointer", gloss="points at the root")],
        registry,
    )

    # Neither is a direct assertion, so the first alias edge wins without borrowing the
    # second one's gloss (which belongs to a different slug).
    assert [(e.slug, e.raw_slug, e.gloss) for e in edges] == [
        ("table-metadata", "metadata-file", "")
    ]


def test_aliasing_promotes_an_ignored_canonical_and_drops_a_distinct_verdict() -> None:
    registry = ignore(ConceptRegistry(), "data")
    registry = mark_distinct(registry, "dataset", "data")
    registry = mark_distinct(registry, "dataset", "schema")

    registry = add_alias(registry, "dataset", "data", "Data")

    assert registry.resolve("dataset") == "data"
    assert not registry.is_ignored("data")
    assert not registry.is_distinct("dataset", "data")
    assert registry.is_distinct("dataset", "schema")  # unrelated verdicts survive
    registry = remove_alias(registry, "dataset")
    assert not registry.is_distinct("dataset", "data")


def test_canonicalize_edges_keeps_per_section_mentions() -> None:
    registry = add_alias(ConceptRegistry(), "metadata-file", "table-metadata", "Table Metadata")

    edges = canonicalize_edges(
        [_edge("metadata-file", "s.a"), _edge("table-metadata", "s.b")], registry
    )

    assert [(e.section_id, e.slug, e.raw_slug) for e in edges] == [
        ("s.a", "table-metadata", "metadata-file"),
        ("s.b", "table-metadata", ""),
    ]
