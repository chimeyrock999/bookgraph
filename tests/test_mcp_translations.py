from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.documents import write_document
from bookgraph.mcp import service
from bookgraph.mcp.service import (
    InvalidIdError,
    ReadingServiceError,
    SectionNotFoundError,
)
from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.sections import write_sections
from bookgraph.workspace import WorkspacePaths


def _section(section_id: str, text: str = "Body.", block_ids: list[str] | None = None) -> Section:
    return Section(
        id=section_id,
        doc_id="deep-work",
        title=section_id.split(".")[-1].title(),
        level=1,
        heading_path=[section_id],
        text=text,
        block_ids=block_ids or [],
    )


def _workspace(tmp_path: Path, *sections: Section) -> WorkspacePaths:
    workspace = WorkspacePaths(tmp_path)
    write_sections(list(sections), workspace.sources_sections / "deep-work")
    return workspace


def test_missing_translation_reports_current_hash(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a"))

    view = service.get_section_translation(workspace, "deep-work", "deep-work.a", "vi")

    assert view.status == "missing"
    assert view.content is None
    assert view.path is None
    assert view.current_section_hash is not None and view.current_section_hash.startswith(
        "sha256:"
    )


def test_write_then_get_returns_fresh_cached_content(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a"))
    missing = service.get_section_translation(workspace, "deep-work", "deep-work.a", "vi")

    written = service.write_section_translation(
        workspace,
        "deep-work",
        "deep-work.a",
        "VI",
        "# Bản dịch",
        includes_assets=True,
        model="claude-test",
        source_section_hash=missing.current_section_hash,
    )

    assert written.status == "fresh"
    assert written.lang == "vi"
    assert written.content is None  # writes do not echo the body back
    assert written.path == str(
        workspace.translations_root / "vi" / "deep-work" / "deep-work.a.md"
    )
    view = service.get_section_translation(workspace, "deep-work", "deep-work.a", "vi")
    assert view.status == "fresh"
    assert view.content == "# Bản dịch"
    assert view.includes_assets is True
    assert view.model == "claude-test"
    assert view.created_at
    assert view.source_section_hash == view.current_section_hash

    meta_only = service.get_section_translation(
        workspace, "deep-work", "deep-work.a", "vi", include_content=False
    )
    assert meta_only.content is None


def test_translation_goes_stale_when_the_section_is_resegmented(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a"))
    service.write_section_translation(workspace, "deep-work", "deep-work.a", "vi", "Dịch.")

    _workspace(tmp_path, _section("deep-work.a", text="Rewritten body."))
    view = service.get_section_translation(workspace, "deep-work", "deep-work.a", "vi")

    assert view.status == "stale"
    assert view.source_section_hash != view.current_section_hash
    assert view.content == "Dịch."  # still served so a caller can decide what to do


def test_write_rejects_a_translation_of_outdated_content(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a"))
    seen = service.get_section_translation(workspace, "deep-work", "deep-work.a", "vi")
    _workspace(tmp_path, _section("deep-work.a", text="Changed meanwhile."))

    with pytest.raises(ReadingServiceError, match="changed since"):
        service.write_section_translation(
            workspace,
            "deep-work",
            "deep-work.a",
            "vi",
            "Dịch.",
            source_section_hash=seen.current_section_hash,
        )
    assert not (workspace.translations_root / "vi").exists()


def test_write_validates_inputs(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a"))

    with pytest.raises(ReadingServiceError, match="empty"):
        service.write_section_translation(workspace, "deep-work", "deep-work.a", "vi", "  ")
    with pytest.raises(SectionNotFoundError):
        service.write_section_translation(workspace, "deep-work", "../../x", "vi", "Dịch.")
    with pytest.raises(InvalidIdError):
        service.write_section_translation(workspace, "deep-work", "deep-work.a", "../vi", "x")
    with pytest.raises(InvalidIdError):
        service.get_section_translation(workspace, "../deep-work", "deep-work.a", "vi")


def test_legacy_cached_body_is_untracked(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a"))
    legacy = workspace.translations_root / "vi" / "deep-work" / "deep-work.a.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("cached before the registry")

    view = service.get_section_translation(workspace, "deep-work", "deep-work.a", "vi")

    assert view.status == "untracked"
    assert view.content == "cached before the registry"
    assert view.includes_assets is None
    assert view.metadata_path is None


def test_section_has_assets_flags_prose_only_translations(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.figs", block_ids=["t1", "img1"]))
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[
                CanonicalBlock(id="t1", type="text", text="Body."),
                CanonicalBlock(id="img1", type="image", text="Figure 1", asset_path="a.jpg"),
            ],
        ),
        workspace.sources_parsed / "deep-work",
    )
    service.write_section_translation(
        workspace, "deep-work", "deep-work.figs", "vi", "Dịch.", includes_assets=False
    )

    view = service.get_section_translation(workspace, "deep-work", "deep-work.figs", "vi")

    assert view.section_has_assets is True
    assert view.includes_assets is False


def test_list_section_artifacts_reports_status_per_translation(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a"), _section("deep-work.b"))
    service.write_section_translation(workspace, "deep-work", "deep-work.a", "vi", "A")
    service.write_section_translation(workspace, "deep-work", "deep-work.b", "vi", "B")
    service.write_section_translation(workspace, "deep-work", "deep-work.a", "fr", "A-fr")
    # Re-segment: b's text changes and a is unchanged.
    _workspace(
        tmp_path,
        _section("deep-work.a"),
        _section("deep-work.b", text="New."),
    )
    gone = workspace.translations_root / "vi" / "deep-work" / "deep-work.gone.md"
    gone.write_text("orphan")

    listing = service.list_section_artifacts(workspace)

    assert [(a.lang, a.section_id, a.status) for a in listing.artifacts] == [
        ("fr", "deep-work.a", "fresh"),
        ("vi", "deep-work.a", "fresh"),
        ("vi", "deep-work.b", "stale"),
        ("vi", "deep-work.gone", "orphaned"),
    ]
    assert all(a.content is None for a in listing.artifacts)
    assert [a.lang for a in service.list_section_artifacts(workspace, lang="fr").artifacts] == [
        "fr"
    ]
    assert service.list_section_artifacts(workspace, doc_id="other").artifacts == []


def test_list_section_artifacts_orphans_unsegmented_documents(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    body = workspace.translations_root / "vi" / "removed-doc" / "removed-doc.a.md"
    body.parent.mkdir(parents=True)
    body.write_text("x")

    listing = service.list_section_artifacts(workspace)

    assert [(a.doc_id, a.status) for a in listing.artifacts] == [("removed-doc", "orphaned")]


def test_list_section_artifacts_rejects_unknown_type(tmp_path: Path) -> None:
    with pytest.raises(ReadingServiceError, match="unknown artifact type"):
        service.list_section_artifacts(WorkspacePaths(tmp_path), artifact_type="summary")
