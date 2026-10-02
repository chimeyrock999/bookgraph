from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from bookgraph import translations
from bookgraph.artifact_hygiene import ArtifactHygieneError
from bookgraph.models import Section, SectionArtifact
from bookgraph.translations import (
    body_content_hash,
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


def test_sidecar_records_the_body_hash(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)

    artifact = write_translation(workspace, _section(), "vi", "Xin chào.")

    assert artifact.content_hash == body_content_hash("Xin chào.".encode())


def test_body_overwritten_after_registration_is_untracked(tmp_path: Path) -> None:
    # A path-convention writer replaces the body behind the registry's back: the old
    # sidecar (model, includes_assets) must no longer vouch for it.
    workspace = WorkspacePaths(tmp_path)
    section = _section()
    write_translation(workspace, section, "vi", "Xin chào.", includes_assets=True)
    paths = translation_paths(workspace, "vi", "deep-work", section.id)
    paths.body.write_text("Bản dịch khác, chỉ có văn xuôi.")

    current = section_content_hash(section)
    state = translation_state(workspace, "vi", "deep-work", section.id, current)

    assert state.status == "untracked"
    assert state.artifact is None


def test_rewrite_drops_the_old_sidecar_before_replacing_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a crash after the new body lands but before its sidecar is written: the
    # previous write's sidecar must not describe the new body.
    workspace = WorkspacePaths(tmp_path)
    section = _section()
    write_translation(workspace, section, "vi", "Bản đầu.", includes_assets=True)
    paths = translation_paths(workspace, "vi", "deep-work", section.id)
    real_write = translations._atomic_write

    def crash_on_sidecar(path: Path, text: str) -> None:
        if path == paths.metadata:
            raise RuntimeError("crash")
        real_write(path, text)

    monkeypatch.setattr(translations, "_atomic_write", crash_on_sidecar)
    with pytest.raises(RuntimeError):
        write_translation(workspace, section, "vi", "Bản hai.", includes_assets=False)

    assert paths.body.read_text() == "Bản hai."
    assert not paths.metadata.exists()
    current = section_content_hash(section)
    state = translation_state(workspace, "vi", "deep-work", section.id, current)
    assert state.status == "untracked"


def test_cache_files_follow_the_umask(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    previous = os.umask(0o022)
    try:
        write_translation(workspace, _section(), "vi", "Xin chào.")
    finally:
        os.umask(previous)
    paths = translation_paths(workspace, "vi", "deep-work", "deep-work.alpha")

    assert stat.S_IMODE(paths.body.stat().st_mode) == 0o644
    assert stat.S_IMODE(paths.metadata.stat().st_mode) == 0o644


def test_body_bytes_on_disk_match_the_recorded_hash(tmp_path: Path) -> None:
    # Bytes, not text mode: newlines are not translated, so the hash survives any platform.
    workspace = WorkspacePaths(tmp_path)
    content = "dòng một\ndòng hai\r\n"

    artifact = write_translation(workspace, _section(), "vi", content)

    body = translation_paths(workspace, "vi", "deep-work", "deep-work.alpha").body
    assert body.read_bytes() == content.encode("utf-8")
    assert artifact.content_hash == body_content_hash(body.read_bytes())


def test_state_carries_the_body_bytes_it_hashed(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    section = _section()
    write_translation(workspace, section, "vi", "Xin chào.")

    current = section_content_hash(section)
    state = translation_state(workspace, "vi", "deep-work", section.id, current)

    assert state.body == "Xin chào.".encode()


def test_sidecar_is_read_as_utf8_bytes_regardless_of_locale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-ASCII model name is stored as raw UTF-8; reading it through the locale
    # encoding (read_text) would garble it or turn a valid registration into untracked.
    workspace = WorkspacePaths(tmp_path)
    section = _section()
    write_translation(workspace, section, "vi", "Xin chào.", model="mô-hình")
    metadata = translation_paths(workspace, "vi", "deep-work", section.id).metadata
    real_read_text = Path.read_text

    def guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == metadata:
            raise AssertionError("sidecar must be decoded as UTF-8 bytes, not locale text")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    current = section_content_hash(section)
    state = translation_state(workspace, "vi", "deep-work", section.id, current)

    assert state.status == "fresh"
    assert state.artifact is not None and state.artifact.model == "mô-hình"


def test_write_refuses_a_body_carrying_job_diagnostics(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    section = _section()
    write_translation(workspace, section, "vi", "Bản cũ.")
    paths = translation_paths(workspace, "vi", section.doc_id, section.id)
    before = (paths.body.read_bytes(), paths.metadata.read_bytes())

    with pytest.raises(ArtifactHygieneError) as excinfo:
        write_translation(
            workspace, section, "vi", "Bản mới.\n\nĐã lưu cache/enrich và mark read: x\n"
        )

    assert [f.code for f in excinfo.value.findings] == ["progress_footer"]
    # Refused before anything is touched: the previous translation stays registered.
    assert (paths.body.read_bytes(), paths.metadata.read_bytes()) == before
