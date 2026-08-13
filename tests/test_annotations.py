from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.annotations import (
    annotation_path,
    build_annotation,
    merge_section_concepts,
    read_annotation,
    read_annotations_for_doc,
    write_annotation,
)
from bookgraph.models import AnnotatedConcept, Section


def _section(section_id: str, title: str, *, doc_id: str = "ddia", text: str = "body") -> Section:
    return Section(
        id=section_id,
        doc_id=doc_id,
        title=title,
        level=1,
        heading_path=[title],
        text=text,
    )


def _concept(title: str, *, slug: str = "", gloss: str = "") -> AnnotatedConcept:
    return AnnotatedConcept(slug=slug, title=title, gloss=gloss)


def test_build_annotation_slugifies_titles() -> None:
    annotation = build_annotation("ddia", "ddia.a", [_concept("Schema Evolution")])

    assert [(c.slug, c.title) for c in annotation.concepts] == [
        ("schema-evolution", "Schema Evolution")
    ]


def test_build_annotation_normalises_a_supplied_slug() -> None:
    annotation = build_annotation(
        "ddia", "ddia.a", [_concept("Schema Evolution", slug="Schema_Evo")]
    )

    assert annotation.concepts[0].slug == "schema-evo"


@pytest.mark.parametrize("title", ["", "   ", "!!!", "untitled"])
def test_build_annotation_rejects_empty_or_untitled_slug(title: str) -> None:
    with pytest.raises(ValueError, match="non-'untitled'|non-empty"):
        build_annotation("ddia", "ddia.a", [_concept(title)])


def test_build_annotation_dedups_by_slug_first_title_and_first_gloss_win() -> None:
    annotation = build_annotation(
        "ddia",
        "ddia.a",
        [
            _concept("Schema Evolution", gloss=""),
            _concept("schema evolution", gloss="why it matters"),  # dup slug, first non-empty gloss
            _concept("Schema Evolution", gloss="ignored second gloss"),
        ],
    )

    assert len(annotation.concepts) == 1
    concept = annotation.concepts[0]
    assert concept.slug == "schema-evolution"
    assert concept.title == "Schema Evolution"  # first occurrence's title wins
    assert concept.gloss == "why it matters"  # first non-empty gloss wins


def test_build_annotation_rejects_a_traversal_doc_id() -> None:
    with pytest.raises(ValueError):
        build_annotation("../escape", "ddia.a", [])


def test_write_read_round_trip(tmp_path: Path) -> None:
    annotation = build_annotation(
        "ddia",
        "ddia.a",
        [_concept("Schema Evolution", gloss="g")],
        summary="a summary",
        model="claude-x",
        created_at="2026-08-13T00:00:00Z",
    )
    path = annotation_path(tmp_path, "ddia", "ddia.a")

    written = write_annotation(annotation, path)

    assert written == path
    assert path == tmp_path / "ddia" / "ddia.a.json"
    assert read_annotation(path) == annotation


def _write(tmp_path: Path, doc_id: str, section_id: str, title: str) -> None:
    write_annotation(
        build_annotation(doc_id, section_id, [_concept(title)]),
        annotation_path(tmp_path, doc_id, section_id),
    )


def test_read_annotations_for_doc_keys_by_section_id(tmp_path: Path) -> None:
    _write(tmp_path, "ddia", "ddia.a", "A")
    _write(tmp_path, "ddia", "ddia.b", "B")
    # A different document's annotation must not leak into ddia's set.
    _write(tmp_path, "other", "other.x", "X")

    annotations = read_annotations_for_doc(tmp_path, "ddia")

    assert set(annotations) == {"ddia.a", "ddia.b"}
    assert annotations["ddia.a"].concepts[0].slug == "a"


def test_read_annotations_for_doc_is_empty_when_absent(tmp_path: Path) -> None:
    assert read_annotations_for_doc(tmp_path, "ddia") == {}


def test_read_annotations_for_doc_skips_corrupt_files(tmp_path: Path) -> None:
    good = annotation_path(tmp_path, "ddia", "ddia.a")
    write_annotation(build_annotation("ddia", "ddia.a", [_concept("A")]), good)
    bad = annotation_path(tmp_path, "ddia", "ddia.b")
    bad.write_text("{ not json\n")

    annotations = read_annotations_for_doc(tmp_path, "ddia")

    assert set(annotations) == {"ddia.a"}


def test_read_annotations_for_doc_skips_wrong_doc_id_payload(tmp_path: Path) -> None:
    # A misplaced file under ddia/ whose payload claims a different doc_id must not
    # be applied when rebuilding ddia (it could otherwise override/prune ddia's
    # Tier-1 concepts via the section_id key).
    write_annotation(
        build_annotation("other", "ddia.a", [_concept("Wrong Doc Concept")]),
        annotation_path(tmp_path, "ddia", "ddia.a"),
    )

    assert read_annotations_for_doc(tmp_path, "ddia") == {}


def test_read_annotations_for_doc_skips_section_id_filename_mismatch(tmp_path: Path) -> None:
    # A file whose payload targets a different section than its filename is misplaced
    # and skipped, so a file cannot masquerade as another section.
    write_annotation(
        build_annotation("ddia", "ddia.b", [_concept("Mismatch")]),
        annotation_path(tmp_path, "ddia", "ddia.a"),
    )

    assert read_annotations_for_doc(tmp_path, "ddia") == {}


def test_merge_annotated_section_wins_and_carries_gloss() -> None:
    sections = [_section("ddia.a", "Schema Evolution", text="Schema Evolution")]
    annotations = {
        "ddia.a": build_annotation(
            "ddia", "ddia.a", [_concept("Log Structured Merge", gloss="core")]
        )
    }

    edges = merge_section_concepts(sections, annotations)

    assert [(e.slug, e.source, e.gloss, e.section_id) for e in edges] == [
        ("log-structured-merge", "agent", "core", "ddia.a")
    ]


def test_merge_empty_annotation_prunes_everything_for_that_section() -> None:
    # The section would yield auto concepts, but an empty annotation zeroes them out.
    sections = [_section("ddia.a", "Schema Evolution", text="Schema Evolution matters")]
    annotations = {"ddia.a": build_annotation("ddia", "ddia.a", [])}

    edges = merge_section_concepts(sections, annotations)

    assert edges == []


def test_merge_non_annotated_section_keeps_auto_concepts() -> None:
    sections = [_section("ddia.a", "Schema Evolution", text="Schema Evolution")]

    edges = merge_section_concepts(sections, {})

    assert edges  # auto extraction produced something
    assert all(e.source == "auto" and e.gloss == "" for e in edges)
    assert any(e.slug == "schema-evolution" for e in edges)


def test_merge_mixed_doc_yields_both_sources() -> None:
    sections = [
        _section("ddia.a", "Schema Evolution", text="Schema Evolution"),
        _section("ddia.b", "Replication", text="Replication"),
    ]
    annotations = {"ddia.a": build_annotation("ddia", "ddia.a", [_concept("Curated", gloss="g")])}

    edges = merge_section_concepts(sections, annotations)

    by_section = {e.section_id: e for e in edges}
    assert by_section["ddia.a"].source == "agent"
    assert by_section["ddia.a"].slug == "curated"
    assert by_section["ddia.b"].source == "auto"
