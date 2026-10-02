from __future__ import annotations

from bookgraph.concept_hygiene import (
    durable_slugs,
    lint_concepts,
    review_queue,
    suggest_merges,
)
from bookgraph.concept_registry import (
    ConceptRegistry,
    add_alias,
    ignore,
    mark_distinct,
    set_canonical,
    title_from_slug,
)
from bookgraph.index.base import Concept, ConceptMention, ConceptNode


def _node(slug: str, doc_count: int = 1, mention_count: int = 1) -> ConceptNode:
    return ConceptNode(
        slug=slug, title=title_from_slug(slug), doc_count=doc_count, mention_count=mention_count
    )


def _concept(slug: str, *sources: str, docs: int = 1) -> Concept:
    mentions = [
        ConceptMention(
            doc_id="iceberg",
            section_id=f"iceberg.s{i}",
            title=f"S{i}",
            source=source,
            gloss="why" if source == "agent" else "",
        )
        for i, source in enumerate(sources)
    ]
    return Concept(node=_node(slug, docs, len(mentions)), mentions=mentions)


def _pairs(nodes: list[ConceptNode], registry: ConceptRegistry | None = None) -> set[tuple]:
    return {
        (s.alias, s.canonical, s.reason)
        for s in suggest_merges(nodes, registry or ConceptRegistry())
    }


def test_suggests_inflection_variants_with_better_connected_canonical() -> None:
    suggestions = suggest_merges(
        [_node("snapshots"), _node("snapshot", doc_count=2, mention_count=5)], ConceptRegistry()
    )

    assert len(suggestions) == 1
    assert (suggestions[0].alias, suggestions[0].canonical) == ("snapshots", "snapshot")
    assert suggestions[0].score == 1.0


def test_suggests_acronyms_subsumed_phrases_and_spelling_variants() -> None:
    pairs = _pairs(
        [
            _node("merge-on-read", mention_count=4),
            _node("mor"),
            _node("metadata-file", mention_count=3),
            _node("latest-metadata-file-pointer"),
            _node("partition-evolution", mention_count=2),
            _node("partiton-evolution"),
        ]
    )

    assert ("mor", "merge-on-read", "acronym") in pairs
    assert (
        "latest-metadata-file-pointer",
        "metadata-file",
        "one concept's words subsume the other's",
    ) in pairs
    assert ("partiton-evolution", "partition-evolution", "near-identical spelling") in pairs


def test_ses_plurals_fold_to_their_singular() -> None:
    pairs = _pairs(
        [
            _node("database", mention_count=3),
            _node("databases"),
            _node("distributed-databases"),
            _node("class", mention_count=2),
            _node("classes"),
        ]
    )

    assert ("databases", "database", "inflection or word-order variant") in pairs
    assert ("classes", "class", "inflection or word-order variant") in pairs
    # Once stemmed, "database" is a subset of "distributed-databases". The subset
    # check now fires, but a lone word inside a phrase is the head-noun case, so it is
    # scored below the default threshold rather than suggested.
    scored = suggest_merges(
        [_node("database", mention_count=3), _node("distributed-databases")],
        ConceptRegistry(),
        threshold=0.0,
    )
    assert [(s.alias, s.canonical, s.score) for s in scored] == [
        ("distributed-databases", "database", 0.4)
    ]


def test_versioned_concepts_are_not_spelling_variants() -> None:
    assert _pairs([_node("format-v1"), _node("format-v2")]) == set()
    assert _pairs([_node("format-version-1"), _node("format-version-2")]) == set()


def test_unrelated_and_head_noun_only_pairs_are_not_suggested() -> None:
    assert _pairs([_node("table"), _node("table-metadata"), _node("snapshot")]) == set()


def test_reviewed_pairs_are_not_suggested_again() -> None:
    nodes = [_node("snapshot"), _node("snapshots"), _node("manifest"), _node("manifests")]
    registry = mark_distinct(ConceptRegistry(), "snapshot", "snapshots")
    registry = ignore(registry, "manifests")

    assert _pairs(nodes, registry) == set()


def test_registry_canonical_side_wins_the_suggestion() -> None:
    registry = set_canonical(ConceptRegistry(), "snapshots", "Snapshots")
    suggestions = suggest_merges(
        [_node("snapshot", doc_count=3, mention_count=9), _node("snapshots")], registry
    )

    assert suggestions[0].canonical == "snapshots"


def test_lint_flags_generic_one_off_granular_and_stale_concepts() -> None:
    registry = add_alias(ConceptRegistry(), "metadata-file", "table-metadata", "Table Metadata")
    findings = lint_concepts(
        [
            _concept("however", "auto", "auto"),
            _concept("incidental-term", "auto"),
            _concept("latest-metadata-file-pointer", "agent"),
            _concept("metadata-file", "agent"),
            _concept("table-metadata", "auto"),  # canonical: exempt from noise rules
            _concept("snapshot", "agent"),
        ],
        registry,
    )

    assert {(f.slug, f.rule, f.severity) for f in findings} == {
        ("however", "generic-term", "warning"),
        ("incidental-term", "one-off", "warning"),
        ("latest-metadata-file-pointer", "over-granular", "info"),
        ("metadata-file", "stale-alias", "warning"),
    }


def test_durable_slugs_drop_only_warning_concepts() -> None:
    concepts = [
        _concept("however", "auto", "auto"),
        _concept("latest-metadata-file-pointer", "agent"),
        _concept("snapshot", "auto", "auto"),
    ]

    assert durable_slugs(concepts, ConceptRegistry()) == {
        "latest-metadata-file-pointer",
        "snapshot",
    }


def test_review_queue_lists_unreviewed_agent_concepts() -> None:
    registry = set_canonical(ConceptRegistry(), "snapshot", "Snapshot")
    registry = add_alias(registry, "metadata-file", "table-metadata", "Table Metadata")

    items = review_queue(
        [
            _concept("catalog-pointer", "agent", "auto"),
            _concept("snapshot", "agent"),
            _concept("metadata-file", "agent"),
            _concept("schema", "auto"),
        ],
        registry,
    )

    assert [(i.slug, i.gloss, i.sections) for i in items] == [
        ("catalog-pointer", "why", ["iceberg.s0"])
    ]
