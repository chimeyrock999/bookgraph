from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.models import Section, SectionArtifact
from bookgraph.translations import (
    iter_translation_keys,
    section_content_hash,
    translation_paths,
    translation_state,
    validate_lang,
    write_translation,
)
from bookgraph.workspace import WorkspacePaths


def _section(text: str = "Hello world.", title: str = "Alpha", **extra: object) -> Section:
    return Section(
        id="deep-work.alpha",
        doc_id="deep-work",
        title=title,
        level=1,
        heading_path=[title],
        text=text,
        **extra,  # type: ignore[arg-type]
    )


def test_content_hash_tracks_title_and_text_but_not_provenance() -> None:
    base = section_content_hash(_section())

    assert base.startswith("sha256:")
    assert section_content_hash(_section(page_start=3, block_ids=["b1"])) == base
    assert section_content_hash(_section(text="Hello world!")) != base
    assert section_content_hash(_section(title="Beta")) != base


def test_validate_lang_normalises_case_and_rejects_traversal() -> None:
    assert validate_lang("pt-BR") == "pt-br"
    with pytest.raises(ValueError):
        validate_lang("../vi")


def test_paths_keep_the_established_body_convention(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)

    paths = translation_paths(workspace, "vi", "deep-work", "deep-work.alpha")

    assert paths.body == tmp_path / "translations" / "vi" / "deep-work" / "deep-work.alpha.md"
    assert paths.metadata == paths.body.with_suffix(".json")
    with pytest.raises(ValueError):
        translation_paths(workspace, "vi", "deep-work", "../escape")


def test_write_registers_a_fresh_translation(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    section = _section()

    artifact = write_translation(workspace, section, "vi", "Xin chào.", includes_assets=True)

    paths = translation_paths(workspace, "vi", "deep-work", section.id)
    assert paths.body.read_text() == "Xin chào."
    assert SectionArtifact.model_validate_json(paths.metadata.read_text()) == artifact
    assert artifact.path == "translations/vi/deep-work/deep-work.alpha.md"
    assert artifact.includes_assets is True
    current = section_content_hash(section)
    state = translation_state(workspace, "vi", "deep-work", section.id, current)
    assert state.status == "fresh"
    assert not list(paths.body.parent.glob(".*.tmp"))


def test_state_is_stale_after_the_section_changes(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    write_translation(workspace, _section(), "vi", "Xin chào.")

    changed = section_content_hash(_section(text="Goodbye."))
    state = translation_state(workspace, "vi", "deep-work", "deep-work.alpha", changed)

    assert state.status == "stale"
    assert state.artifact is not None


def test_state_missing_untracked_and_orphaned(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    current = section_content_hash(_section())
    paths = translation_paths(workspace, "vi", "deep-work", "deep-work.alpha")

    assert translation_state(workspace, "vi", "deep-work", "deep-work.alpha", current).status == (
        "missing"
    )

    # A body cached by the pre-registry path convention: reusable, freshness unknown.
    paths.body.parent.mkdir(parents=True)
    paths.body.write_text("legacy")
    assert translation_state(workspace, "vi", "deep-work", "deep-work.alpha", current).status == (
        "untracked"
    )

    # The section no longer exists.
    assert translation_state(workspace, "vi", "deep-work", "deep-work.alpha", None).status == (
        "orphaned"
    )


def test_corrupt_or_misplaced_sidecar_is_untracked(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    section = _section()
    current = section_content_hash(section)
    write_translation(workspace, section, "vi", "Xin chào.")
    paths = translation_paths(workspace, "vi", "deep-work", section.id)

    # A sidecar copied from another language must not vouch for this body.
    other = SectionArtifact.model_validate_json(paths.metadata.read_text())
    paths.metadata.write_text(other.model_copy(update={"lang": "fr"}).model_dump_json())
    assert translation_state(workspace, "vi", "deep-work", section.id, current).status == (
        "untracked"
    )

    paths.metadata.write_text("{not json")
    assert translation_state(workspace, "vi", "deep-work", section.id, current).status == (
        "untracked"
    )


def test_sidecar_without_body_is_missing(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    section = _section()
    write_translation(workspace, section, "vi", "Xin chào.")
    translation_paths(workspace, "vi", "deep-work", section.id).body.unlink()

    current = section_content_hash(section)
    state = translation_state(workspace, "vi", "deep-work", section.id, current)

    assert state.status == "missing"
    assert state.artifact is None


def test_iter_keys_lists_bodies_with_filters(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    write_translation(workspace, _section(), "vi", "a")
    write_translation(workspace, _section(), "fr", "b")
    stray = workspace.translations_root / "vi" / "deep-work" / "orphan-sidecar.json"
    stray.write_text("{}")
    (workspace.translations_root / "Not A Slug").mkdir()

    assert list(iter_translation_keys(workspace)) == [
        ("fr", "deep-work", "deep-work.alpha"),
        ("vi", "deep-work", "deep-work.alpha"),
    ]
    assert list(iter_translation_keys(workspace, lang="VI")) == [
        ("vi", "deep-work", "deep-work.alpha"),
    ]
    assert list(iter_translation_keys(workspace, doc_id="other")) == []
