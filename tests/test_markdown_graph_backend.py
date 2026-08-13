from __future__ import annotations

from pathlib import Path

from bookgraph.models import Section
from bookgraph.wiki_backends.markdown_graph import MarkdownGraphBackend, extract_concepts


def _sections() -> list[Section]:
    return [
        Section(
            id="iceberg.intro",
            doc_id="iceberg",
            title="Apache Iceberg",
            level=1,
            heading_path=["Apache Iceberg"],
            text="Apache Iceberg supports schema evolution and partition evolution.",
            prev_id=None,
            next_id="iceberg.metadata",
            block_ids=["b1"],
        ),
        Section(
            id="iceberg.metadata",
            doc_id="iceberg",
            title="Metadata Tables",
            level=2,
            heading_path=["Apache Iceberg", "Metadata Tables"],
            text="Metadata Tables expose snapshots, manifests, and schema evolution history.",
            prev_id="iceberg.intro",
            next_id=None,
            block_ids=["b2"],
        ),
    ]


def test_markdown_graph_backend_writes_book_sections_index_and_concepts(tmp_path: Path) -> None:
    output_dir = tmp_path / "wiki" / "books" / "iceberg"

    written = MarkdownGraphBackend().compile_book(_sections(), output_dir)

    assert written == output_dir
    index = (output_dir / "README.md").read_text()
    assert "# iceberg" in index
    assert "- [Apache Iceberg](sections/iceberg.intro.md)" in index
    assert "  - [Metadata Tables](sections/iceberg.metadata.md)" in index
    assert "## Concepts" in index
    assert "../../concepts/apache-iceberg.md" in index

    section = (output_dir / "sections" / "iceberg.intro.md").read_text()
    assert "id: \"iceberg.intro\"" in section
    assert "# Apache Iceberg" in section
    assert "## Linked concepts" in section
    assert "[[apache-iceberg|Apache Iceberg]]" in section

    concept = (tmp_path / "wiki" / "concepts" / "apache-iceberg.md").read_text()
    assert "# Apache Iceberg" in concept
    assert "- [Apache Iceberg](../books/iceberg/sections/iceberg.intro.md)" in concept
    assert "- [Metadata Tables](../books/iceberg/sections/iceberg.metadata.md)" in concept


def test_extract_concepts_is_deterministic_and_slug_safe() -> None:
    concepts = extract_concepts(_sections())

    assert [concept.slug for concept in concepts][:3] == [
        "apache-iceberg",
        "evolution",
        "metadata-tables",
    ]
    assert all("/" not in concept.slug and ".." not in concept.slug for concept in concepts)
