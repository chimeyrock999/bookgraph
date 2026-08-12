from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.models import Section
from bookgraph.sections import render_section_markdown, write_sections


def _section(section_id: str, title: str = "A Title", **overrides: object) -> Section:
    fields: dict[str, object] = {
        "id": section_id,
        "doc_id": "ddia",
        "title": title,
        "level": 2,
        "heading_path": ["Root", title],
        "page_start": 10,
        "page_end": 11,
        "text": "Body text.",
        "block_ids": ["b1", "b2"],
    }
    fields.update(overrides)
    return Section(**fields)  # type: ignore[arg-type]


def test_write_sections_writes_one_manifest_line_per_section(tmp_path: Path) -> None:
    sections = [
        _section("ddia.a", "A", next_id="ddia.b"),
        _section("ddia.b", "B", prev_id="ddia.a"),
    ]

    output = write_sections(sections, tmp_path)

    lines = output.manifest.read_text().splitlines()
    assert len(lines) == 2
    assert [Section.model_validate_json(line).id for line in lines] == ["ddia.a", "ddia.b"]
    # Manifest ends with a trailing newline, no blank lines.
    assert output.manifest.read_text().endswith("}\n")


def test_write_sections_writes_one_markdown_file_per_section(tmp_path: Path) -> None:
    sections = [_section("ddia.a", "A"), _section("ddia.b", "B")]

    output = write_sections(sections, tmp_path)

    assert output.markdown == [tmp_path / "ddia.a.md", tmp_path / "ddia.b.md"]
    assert all(path.is_file() for path in output.markdown)


def test_write_sections_refuses_duplicate_ids(tmp_path: Path) -> None:
    sections = [_section("ddia.dup", "First"), _section("ddia.dup", "Second")]

    with pytest.raises(ValueError, match="duplicate section ids.*ddia.dup"):
        write_sections(sections, tmp_path)


@pytest.mark.parametrize("unsafe_id", ["../escape", "a/b", "..", "UPPER", "with space"])
def test_write_sections_refuses_path_unsafe_ids(tmp_path: Path, unsafe_id: str) -> None:
    sections = [_section(unsafe_id, "Escape")]

    with pytest.raises(ValueError, match="filename-safe"):
        write_sections(sections, tmp_path)

    # Nothing is written when an id is path-unsafe, so no escape is possible.
    assert not (tmp_path / "sections.jsonl").exists()
    assert not (tmp_path.parent / "escape.md").exists()


def test_write_sections_handles_no_sections(tmp_path: Path) -> None:
    output = write_sections([], tmp_path)

    assert output.manifest.read_text() == ""
    assert output.markdown == []


def test_render_section_markdown_carries_provenance_frontmatter() -> None:
    section = _section("ddia.storage", "Storage", block_ids=["b1", "b2", "b3"])

    rendered = render_section_markdown(section)

    assert rendered.startswith("---\n")
    assert '\nid: "ddia.storage"\n' in rendered
    assert '\nblock_ids: ["b1", "b2", "b3"]\n' in rendered
    assert '\nprev_id: null\n' in rendered
    assert "\n---\n\n## Storage\n\nBody text.\n" in rendered


def test_render_section_markdown_survives_special_characters_in_title() -> None:
    section = _section("ddia.tricky", 'Chapter 3: "Storage" & Retrieval')

    rendered = render_section_markdown(section)

    # The title is JSON-quoted in frontmatter, so the colon/quotes stay valid YAML.
    assert '\ntitle: "Chapter 3: \\"Storage\\" & Retrieval"\n' in rendered
    assert '## Chapter 3: "Storage" & Retrieval' in rendered


def test_render_section_markdown_omits_body_when_text_is_empty() -> None:
    section = _section("ddia.empty", "Empty", text="")

    rendered = render_section_markdown(section)

    assert rendered.endswith("---\n\n## Empty\n")
