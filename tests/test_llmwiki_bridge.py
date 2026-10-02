from __future__ import annotations

import json
from pathlib import Path

from bookgraph.llmwiki_bridge import (
    ensure_project_config,
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
    result = stage_sections(
        [_section("deep-work.intro", "Intro"), _section("deep-work.ch1", "Chapter 1")], sources
    )

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


def test_ensure_project_config_creates_recursive_config_for_new_project(
    tmp_path: Path,
) -> None:
    root = tmp_path / "llmwiki"

    assert ensure_project_config(root) is True

    config = json.loads((root / ".llmwiki" / "config.json").read_text())
    assert config == {"version": 1, "sources": {"recursive": True}}
    # Re-running keeps the nested layout and leaves the config untouched.
    assert ensure_project_config(root) is True


def test_ensure_project_config_keeps_flat_layout_for_existing_project(
    tmp_path: Path,
) -> None:
    # A project staged before nested layout (flat sources/, no config) keeps the
    # flat layout: nesting it now would compile every section twice.
    root = tmp_path / "llmwiki"
    (root / "sources").mkdir(parents=True)
    (root / "sources" / "deep-work.intro.md").write_text("old\n")

    assert ensure_project_config(root) is False
    assert not (root / ".llmwiki" / "config.json").exists()


def test_ensure_project_config_never_rewrites_a_user_config(tmp_path: Path) -> None:
    root = tmp_path / "llmwiki"
    config = root / ".llmwiki" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"version": 1, "review": {"hold": "all"}}\n')

    assert ensure_project_config(root) is False
    assert config.read_text() == '{"version": 1, "review": {"hold": "all"}}\n'


def test_ensure_project_config_follows_an_existing_recursive_config(tmp_path: Path) -> None:
    root = tmp_path / "llmwiki"
    (root / "sources").mkdir(parents=True)
    config = root / ".llmwiki" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"version": 1, "sources": {"recursive": true, "exclude": ["x"]}}\n')

    assert ensure_project_config(root) is True


def test_stage_sections_nested_groups_by_document(tmp_path: Path) -> None:
    sources = tmp_path / "sources"

    result = stage_sections([_section("deep-work.intro", "Intro")], sources, nested=True)

    path = sources / "deep-work" / "deep-work.intro.md"
    assert result.staged == [path]
    assert path.is_file()
    # Regular files, never symlinks: llmwiki >= 1.3 skips symlinked sources.
    assert not path.is_symlink()
    assert not (sources / "deep-work.intro.md").exists()


def test_stage_sections_writes_regular_files(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    result = stage_sections([_section("deep-work.intro", "Intro")], sources)

    assert all(path.is_file() and not path.is_symlink() for path in result.staged)
