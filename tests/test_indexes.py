from __future__ import annotations

from pathlib import Path

from bookgraph.indexes import (
    build_section_index,
    index_path,
    read_index,
    tokenize,
    write_index,
)
from bookgraph.models import Section
from bookgraph.workspace import WorkspacePaths


def _section(section_id: str, title: str, text: str) -> Section:
    return Section(
        id=section_id,
        doc_id="deep-work",
        title=title,
        level=1,
        heading_path=[title],
        text=text,
    )


def test_tokenize_lowercases_and_splits_on_non_alphanumeric() -> None:
    assert tokenize("Storage-Engines, and B-Trees!") == [
        "storage",
        "engines",
        "and",
        "b",
        "trees",
    ]


def test_build_section_index_counts_term_frequencies_across_title_and_text() -> None:
    index = build_section_index(
        "deep-work",
        [
            _section("deep-work.a", "Storage engines", "storage storage index"),
            _section("deep-work.b", "Replication", "leaders and followers"),
        ],
    )

    assert index.doc_id == "deep-work"
    # "storage" appears once in the title and twice in the body of section a.
    assert index.postings["storage"] == {"deep-work.a": 3}
    assert index.postings["index"] == {"deep-work.a": 1}
    assert index.postings["leaders"] == {"deep-work.b": 1}
    assert [section.id for section in index.sections] == ["deep-work.a", "deep-work.b"]


def test_write_and_read_index_round_trips(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    index = build_section_index(
        "deep-work",
        [_section("deep-work.a", "Storage", "storage text")],
    )

    path = write_index(index, index_path(workspace, "deep-work"))

    assert path == workspace.indexes_root / "sections" / "deep-work.json"
    assert path.is_file()
    assert read_index(path) == index
