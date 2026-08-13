from __future__ import annotations

from pathlib import Path

from bookgraph.models import Section
from bookgraph.sections import write_sections
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


def _compile(
    tmp_path: Path,
    sections: list[Section],
    doc_id: str | None = None,
    *,
    explicit_concepts_dir: bool = True,
) -> None:
    resolved_doc_id = doc_id or sections[0].doc_id
    workspace = tmp_path / "workspace"
    write_sections(sections, workspace / "sources" / "sections" / resolved_doc_id)
    concepts_dir = workspace / "wiki" / "concepts" if explicit_concepts_dir else None
    MarkdownGraphBackend().compile_book(
        sections,
        workspace / "wiki" / "books" / resolved_doc_id,
        concepts_dir,
    )


def test_markdown_graph_backend_writes_book_sections_index_and_concepts(tmp_path: Path) -> None:
    _compile(tmp_path, _sections())
    workspace = tmp_path / "workspace"
    output_dir = workspace / "wiki" / "books" / "iceberg"

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

    concept = (workspace / "wiki" / "concepts" / "apache-iceberg.md").read_text()
    assert "# Apache Iceberg" in concept
    assert "section_count: 2" in concept
    assert "- [Apache Iceberg](../books/iceberg/sections/iceberg.intro.md)" in concept
    assert "- [Metadata Tables](../books/iceberg/sections/iceberg.metadata.md)" in concept


def test_markdown_graph_backend_preserves_cross_book_concept_backlinks(tmp_path: Path) -> None:
    _compile(tmp_path, _sections("iceberg"))
    _compile(tmp_path, _sections("spark"))

    concept = (tmp_path / "workspace" / "wiki" / "concepts" / "apache-iceberg.md").read_text()

    assert "../books/iceberg/sections/iceberg.intro.md" in concept
    assert "../books/spark/sections/spark.intro.md" in concept
    assert "section_count: 4" in concept


def test_markdown_graph_backend_removes_stale_concepts_for_recompiled_book(
    tmp_path: Path,
) -> None:
    _compile(tmp_path, _sections("iceberg"))
    stale = tmp_path / "workspace" / "wiki" / "concepts" / "apache-iceberg.md"
    assert stale.is_file()

    _compile(
        tmp_path,
        [
            Section(
                id="iceberg.other",
                doc_id="iceberg",
                title="Other Topic",
                level=1,
                heading_path=["Other Topic"],
                text="fresh unrelated content",
            )
        ],
    )

    assert not stale.exists()


def test_markdown_graph_backend_updates_only_current_doc_in_shared_concept(
    tmp_path: Path,
) -> None:
    _compile(tmp_path, _sections("iceberg"))
    _compile(tmp_path, _sections("spark"))

    _compile(
        tmp_path,
        [
            Section(
                id="iceberg.other",
                doc_id="iceberg",
                title="Other Topic",
                level=1,
                heading_path=["Other Topic"],
                text="fresh unrelated content",
            )
        ],
    )

    concept = (tmp_path / "workspace" / "wiki" / "concepts" / "apache-iceberg.md").read_text()
    assert "../books/iceberg/" not in concept
    assert "../books/spark/sections/spark.intro.md" in concept
    assert "section_count: 2" in concept


def test_markdown_graph_backend_survives_stale_cross_book_section_ids(
    tmp_path: Path,
) -> None:
    _compile(tmp_path, _sections("iceberg"))
    _compile(tmp_path, _sections("spark"))

    # Spark has been re-segmented on disk since its own last wiki compile. The
    # concept page still stores the old spark.intro/spark.metadata mentions until
    # spark is compiled again; recompiling another book must not read Spark's
    # current source manifest and crash on missing ids.
    write_sections(
        [
            Section(
                id="spark.renamed",
                doc_id="spark",
                title="Apache Iceberg",
                level=1,
                heading_path=["Apache Iceberg"],
                text="Apache Iceberg after re-segmentation.",
            )
        ],
        tmp_path / "workspace" / "sources" / "sections" / "spark",
    )

    _compile(tmp_path, _sections("iceberg"))

    concept = (tmp_path / "workspace" / "wiki" / "concepts" / "apache-iceberg.md").read_text()
    assert "../books/iceberg/sections/iceberg.intro.md" in concept
    assert "../books/spark/sections/spark.intro.md" in concept


def test_markdown_graph_backend_compile_book_default_concepts_dir_does_not_crash(
    tmp_path: Path,
) -> None:
    _compile(tmp_path, _sections("iceberg"), explicit_concepts_dir=False)

    concept = (
        tmp_path
        / "workspace"
        / "wiki"
        / "books"
        / "iceberg"
        / "concepts"
        / "apache-iceberg.md"
    ).read_text()
    assert "../books/iceberg/sections/iceberg.intro.md" in concept


def test_markdown_graph_backend_escapes_wiki_link_titles(tmp_path: Path) -> None:
    _compile(
        tmp_path,
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
    )

    section = (
        tmp_path / "workspace" / "wiki" / "books" / "pipes" / "sections" / "pipes.foo.md"
    ).read_text()
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
