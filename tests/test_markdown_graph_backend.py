from __future__ import annotations

from pathlib import Path

from bookgraph.models import Section
from bookgraph.wiki_backends.markdown_graph import MarkdownGraphBackend, extract_concepts


def _sections(doc_id: str = "iceberg") -> list[Section]:
    return [
        Section(
            id=f"{doc_id}.intro",
            doc_id=doc_id,
            title="Apache Iceberg",
            level=1,
            heading_path=["Apache Iceberg"],
            text="Apache Iceberg supports schema evolution and partition evolution.",
            prev_id=None,
            next_id=f"{doc_id}.metadata",
            block_ids=["b1"],
        ),
        Section(
            id=f"{doc_id}.metadata",
            doc_id=doc_id,
            title="Metadata Tables",
            level=2,
            heading_path=["Apache Iceberg", "Metadata Tables"],
            text="Metadata Tables expose snapshots, manifests, and schema evolution history.",
            prev_id=f"{doc_id}.intro",
            next_id=None,
            block_ids=["b2"],
        ),
    ]


def test_markdown_graph_backend_writes_book_sections_index_and_wikilinks(tmp_path: Path) -> None:
    output_dir = tmp_path / "wiki" / "books" / "iceberg"

    written = MarkdownGraphBackend().compile_book(_sections(), output_dir)

    assert written == output_dir
    index = (output_dir / "README.md").read_text()
    assert "# iceberg" in index
    assert "- [Apache Iceberg](sections/iceberg.intro.md)" in index
    assert "  - [Metadata Tables](sections/iceberg.metadata.md)" in index
    assert "## Concepts" in index
    assert "[[apache-iceberg|Apache Iceberg]]" in index

    section = (output_dir / "sections" / "iceberg.intro.md").read_text()
    assert "id: \"iceberg.intro\"" in section
    assert "# Apache Iceberg" in section
    assert "## Linked concepts" in section
    assert "[[apache-iceberg|Apache Iceberg]]" in section
    assert not (tmp_path / "wiki" / "concepts" / "apache-iceberg.md").exists()


def test_markdown_graph_backend_is_stateless_across_books(tmp_path: Path) -> None:
    backend = MarkdownGraphBackend()
    backend.compile_book(_sections("iceberg"), tmp_path / "wiki" / "books" / "iceberg")
    backend.compile_book(_sections("spark"), tmp_path / "wiki" / "books" / "spark")

    assert (tmp_path / "wiki" / "books" / "iceberg" / "sections" / "iceberg.intro.md").is_file()
    assert (tmp_path / "wiki" / "books" / "spark" / "sections" / "spark.intro.md").is_file()
    assert not (tmp_path / "wiki" / "concepts").exists()


def test_markdown_graph_backend_escapes_wiki_link_titles(tmp_path: Path) -> None:
    output_dir = tmp_path / "wiki" / "books" / "pipes"
    MarkdownGraphBackend().compile_book(
        [
            Section(
                id="pipes.foo",
                doc_id="pipes",
                title="Foo | Bar",
                level=1,
                heading_path=["Foo | Bar"],
                text="Foo Bar content",
            )
        ],
        output_dir,
    )

    section = (output_dir / "sections" / "pipes.foo.md").read_text()
    assert "[[foo-bar-2|Foo / Bar]]" in section
    assert "[[foo-bar|Foo | Bar]]" not in section


def test_extract_concepts_is_deterministic_and_slug_safe() -> None:
    concepts = extract_concepts(_sections())

    assert [concept.slug for concept in concepts][:3] == [
        "apache-iceberg",
        "evolution",
        "schema",
    ]
    assert all("/" not in concept.slug and ".." not in concept.slug for concept in concepts)
