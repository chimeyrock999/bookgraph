from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bookgraph.annotations import annotation_path, build_annotation, write_annotation
from bookgraph.cli import app
from bookgraph.index.sqlite import SqliteIndexBackend, db_path
from bookgraph.models import AnnotatedConcept, Section
from bookgraph.sections import write_sections
from bookgraph.workspace import WorkspacePaths

runner = CliRunner()


def _section(section_id: str, doc_id: str, title: str, text: str) -> Section:
    return Section(
        id=section_id,
        doc_id=doc_id,
        title=title,
        level=1,
        heading_path=[title],
        text=text,
    )


def _init_workspace(tmp_path: Path) -> WorkspacePaths:
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return WorkspacePaths(tmp_path)


def test_index_build_writes_index_for_one_document(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    write_sections(
        [_section("deep-work.a", "deep-work", "Storage", "storage storage index")],
        workspace.sources_sections / "deep-work",
    )

    result = runner.invoke(app, ["index", "build", str(tmp_path), "--doc-id", "deep-work"])

    assert result.exit_code == 0, result.output
    backend = SqliteIndexBackend()
    assert db_path(workspace).is_file()
    assert backend.indexed_doc_ids(workspace) == {"deep-work"}
    # The build also persists the section graph.
    graph = backend.load_graph(workspace, "deep-work")
    assert graph is not None
    assert [node.id for node in graph.nodes] == ["deep-work.a"]


def test_index_build_indexes_every_segmented_document(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    write_sections(
        [_section("deep-work.a", "deep-work", "Storage", "storage text")],
        workspace.sources_sections / "deep-work",
    )
    write_sections(
        [_section("ddia.x", "ddia", "Replication", "leaders and followers")],
        workspace.sources_sections / "ddia",
    )

    result = runner.invoke(app, ["index", "build", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert SqliteIndexBackend().indexed_doc_ids(workspace) == {"ddia", "deep-work"}


def test_index_build_errors_when_nothing_is_segmented(tmp_path: Path) -> None:
    _init_workspace(tmp_path)

    result = runner.invoke(app, ["index", "build", str(tmp_path)])

    assert result.exit_code != 0
    assert "No segmented documents" in result.output


def test_index_build_rejects_traversal_doc_id(tmp_path: Path) -> None:
    _init_workspace(tmp_path)

    result = runner.invoke(app, ["index", "build", str(tmp_path), "--doc-id", "../escape"])

    assert result.exit_code != 0


def test_index_concepts_renders_cross_book_pages(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    write_sections(
        [_section("ddia.a", "ddia", "Schema Evolution", "text")],
        workspace.sources_sections / "ddia",
    )
    write_sections(
        [_section("deep-work.a", "deep-work", "Schema Evolution", "text")],
        workspace.sources_sections / "deep-work",
    )
    assert runner.invoke(app, ["index", "build", str(tmp_path)]).exit_code == 0

    result = runner.invoke(app, ["index", "concepts", str(tmp_path)])

    assert result.exit_code == 0, result.output
    page = workspace.wiki_concepts / "schema-evolution.md"
    assert page.is_file()
    body = page.read_text()
    assert body.startswith("# Schema Evolution")
    assert "Mentioned in 2 books" in body
    # Cross-book backlinks with relative links into the book pages.
    assert "../books/ddia/sections/ddia.a.md" in body
    assert "../books/deep-work/sections/deep-work.a.md" in body


def test_index_build_reports_location_on_partial_failure(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    # "aaa" is valid and indexed first; "zzz" has a corrupt manifest that aborts the
    # run mid-loop — the summary must still report where the (partial) index lives.
    write_sections(
        [_section("aaa.a", "aaa", "Storage", "text")], workspace.sources_sections / "aaa"
    )
    (workspace.sources_sections / "zzz").mkdir(parents=True, exist_ok=True)
    (workspace.sources_sections / "zzz" / "sections.jsonl").write_text("{ not json\n")

    result = runner.invoke(app, ["index", "build", str(tmp_path)])

    assert result.exit_code != 0
    assert "doc_id: aaa" in result.output  # the earlier document was indexed
    assert "index:" in result.output  # ...and its location is still reported


def test_index_concepts_warns_when_book_pages_not_compiled(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    write_sections(
        [_section("ddia.a", "ddia", "Schema Evolution", "text")],
        workspace.sources_sections / "ddia",
    )
    assert runner.invoke(app, ["index", "build", str(tmp_path)]).exit_code == 0

    # No `wiki compile` run yet: every backlink targets a not-yet-materialized page.
    result = runner.invoke(app, ["index", "concepts", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "warning:" in result.output
    assert "wiki compile" in result.output

    # Materialize the book section page the backlinks point at; the warning clears.
    book_page = workspace.wiki_books / "ddia" / "sections" / "ddia.a.md"
    book_page.parent.mkdir(parents=True, exist_ok=True)
    book_page.write_text("# Schema Evolution\n")
    result = runner.invoke(app, ["index", "concepts", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "warning:" not in result.output


def test_index_concepts_merges_slug_colliding_terms_into_one_page(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    # "Schema Evolution" (title-case phrase) and the token "schema-evolution" both
    # slugify to "schema-evolution": they must be one concept, not two pages, so the
    # cross-book join key stays stable rather than gaining a per-build "-2" suffix.
    write_sections(
        [_section("ddia.a", "ddia", "Schema Evolution", "the schema-evolution identifier")],
        workspace.sources_sections / "ddia",
    )
    assert runner.invoke(app, ["index", "build", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["index", "concepts", str(tmp_path)]).exit_code == 0

    assert (workspace.wiki_concepts / "schema-evolution.md").is_file()
    assert not (workspace.wiki_concepts / "schema-evolution-2.md").exists()


def test_index_concepts_rewrites_the_directory(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    write_sections(
        [_section("ddia.a", "ddia", "Replication", "text")],
        workspace.sources_sections / "ddia",
    )
    assert runner.invoke(app, ["index", "build", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["index", "concepts", str(tmp_path)]).exit_code == 0
    assert (workspace.wiki_concepts / "replication.md").is_file()

    # Re-segment the document with a different concept, rebuild, re-render.
    write_sections(
        [_section("ddia.a", "ddia", "Consensus", "text")],
        workspace.sources_sections / "ddia",
    )
    assert runner.invoke(app, ["index", "build", str(tmp_path), "--doc-id", "ddia"]).exit_code == 0
    assert runner.invoke(app, ["index", "concepts", str(tmp_path)]).exit_code == 0

    assert (workspace.wiki_concepts / "consensus.md").is_file()
    assert not (workspace.wiki_concepts / "replication.md").exists()  # stale page removed


def test_index_concepts_renders_agent_gloss_and_marker(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    write_sections(
        [_section("ddia.a", "ddia", "Storage Engines", "storage text")],
        workspace.sources_sections / "ddia",
    )
    # Annotate the section so its concept becomes an agent-verified, glossed backlink.
    write_annotation(
        build_annotation(
            "ddia",
            "ddia.a",
            [AnnotatedConcept(slug="", title="Log Structured Merge", gloss="the core idea")],
        ),
        annotation_path(workspace.annotations_root, "ddia", "ddia.a"),
    )
    assert runner.invoke(app, ["index", "build", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["index", "concepts", str(tmp_path)]).exit_code == 0

    page = (workspace.wiki_concepts / "log-structured-merge.md").read_text()
    assert "— the core idea (agent-verified)" in page


def test_index_concepts_renders_section_summary_as_blockquote(tmp_path: Path) -> None:
    # The durable concept note carries each mentioning section's Tier-2 summary as an
    # indented blockquote, so the page reads as long-form context, not only glosses.
    workspace = _init_workspace(tmp_path)
    write_sections(
        [_section("ddia.a", "ddia", "Schema Evolution", "text")],
        workspace.sources_sections / "ddia",
    )
    summary = "Schemas change; readers/writers must cope."
    write_annotation(
        build_annotation("ddia", "ddia.a", None, summary=summary),
        annotation_path(workspace.annotations_root, "ddia", "ddia.a"),
    )
    assert runner.invoke(app, ["index", "build", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["index", "concepts", str(tmp_path)]).exit_code == 0

    page = (workspace.wiki_concepts / "schema-evolution.md").read_text()
    assert "  > Schemas change; readers/writers must cope." in page


def test_index_concepts_omits_marker_for_auto_backlinks(tmp_path: Path) -> None:
    workspace = _init_workspace(tmp_path)
    write_sections(
        [_section("ddia.a", "ddia", "Schema Evolution", "text")],
        workspace.sources_sections / "ddia",
    )
    assert runner.invoke(app, ["index", "build", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["index", "concepts", str(tmp_path)]).exit_code == 0

    page = (workspace.wiki_concepts / "schema-evolution.md").read_text()
    assert "(agent-verified)" not in page


def test_index_concepts_errors_without_an_index(tmp_path: Path) -> None:
    _init_workspace(tmp_path)

    result = runner.invoke(app, ["index", "concepts", str(tmp_path)])

    assert result.exit_code != 0
    assert "No concepts" in result.output
