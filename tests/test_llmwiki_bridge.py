from __future__ import annotations

from pathlib import Path

from bookgraph.llmwiki_bridge import (
    render_llmwiki_source,
    stage_sections,
    staged_source_name,
)
from bookgraph.models import Section


def _section(section_id: str, title: str, text: str = "Body.") -> Section:
    return Section(
        id=section_id,
        doc_id="deep-work",
        title=title,
        level=2,
        heading_path=["Intro", title],
        text=text,
    )


def test_staged_source_name_mirrors_section_id() -> None:
    assert staged_source_name(_section("deep-work.intro", "Intro")) == "deep-work.intro.md"


def test_render_source_carries_provenance_and_body() -> None:
    text = render_llmwiki_source(_section("deep-work.intro", "Intro", "Hello."))

    assert 'title: "Intro"' in text
    assert 'bookgraph_doc_id: "deep-work"' in text
    assert 'bookgraph_section_id: "deep-work.intro"' in text
    assert "## Intro" in text
    assert "Hello." in text


def test_render_source_frontmatter_survives_colons_in_title() -> None:
    text = render_llmwiki_source(_section("deep-work.intro", "Focus: Rules of Attention"))

    # JSON-encoded value keeps a colon-bearing title from corrupting the YAML.
    assert 'title: "Focus: Rules of Attention"' in text


def test_stage_sections_writes_one_file_per_section(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    result = stage_sections([_section("deep-work.intro", "Intro"),
                             _section("deep-work.ch1", "Chapter 1")], sources)

    assert result.sources_dir == sources
    assert len(result.staged) == 2
    assert not result.unchanged
    assert (sources / "deep-work.intro.md").is_file()
    assert (sources / "deep-work.ch1.md").is_file()


def test_stage_sections_is_idempotent_for_unchanged_sections(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sections = [_section("deep-work.intro", "Intro")]
    first = stage_sections(sections, sources)
    mtime = (sources / "deep-work.intro.md").stat().st_mtime_ns

    second = stage_sections(sections, sources)

    assert len(first.staged) == 1
    assert not second.staged
    assert len(second.unchanged) == 1
    # Unchanged content must leave the file (and its mtime) untouched so llmwiki's
    # incremental compile skips it.
    assert (sources / "deep-work.intro.md").stat().st_mtime_ns == mtime


def test_stage_sections_rewrites_only_changed_sections(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    stage_sections([_section("deep-work.intro", "Intro", "v1")], sources)

    result = stage_sections([_section("deep-work.intro", "Intro", "v2")], sources)

    assert len(result.staged) == 1
    assert "v2" in (sources / "deep-work.intro.md").read_text()
