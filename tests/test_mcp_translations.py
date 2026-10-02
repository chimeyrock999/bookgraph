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
    assert view.current_section_hash is not None and view.current_section_hash.startswith("sha256:")


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
    assert written.path == str(workspace.translations_root / "vi" / "deep-work" / "deep-work.a.md")
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


def test_get_serves_the_body_it_validated_not_a_second_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A rewrite landing after the status check must not pair new text with the old sidecar.
    workspace = _workspace(tmp_path, _section("deep-work.a"))
    service.write_section_translation(workspace, "deep-work", "deep-work.a", "vi", "Bản A")

    body = workspace.translations_root / "vi" / "deep-work" / "deep-work.a.md"
    real_read_text = Path.read_text

    def guarded_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == body:
            raise AssertionError("content must come from the validated bytes")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    view = service.get_section_translation(workspace, "deep-work", "deep-work.a", "vi")

    assert view.status == "fresh"
    assert view.content == "Bản A"


LINKED = "See [the models chapter](ch03.html#sec_models) and [the figure](#fig_query)."


def test_translation_view_flags_changed_link_targets(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", text=LINKED))

    written = service.write_section_translation(
        workspace,
        "deep-work",
        "deep-work.a",
        "vi",
        "Xem [chương mô hình](ch03.html#sec_mo_hinh) và [hình](#fig_query).",
    )

    # The write is kept (the body is the deliverable) but reported, so the job can fix it.
    assert written.status == "fresh"
    assert [(i.kind, i.target, i.change) for i in written.structure_issues] == [
        ("link", "ch03.html#sec_models", "missing"),
        ("link", "ch03.html#sec_mo_hinh", "added"),
    ]
    view = service.get_section_translation(workspace, "deep-work", "deep-work.a", "vi")
    assert len(view.structure_issues) == 2
    listed = service.list_section_artifacts(workspace, doc_id="deep-work")
    assert len(listed.artifacts[0].structure_issues) == 2


def test_translated_labels_with_preserved_targets_have_no_structure_issues(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", text=LINKED))

    written = service.write_section_translation(
        workspace,
        "deep-work",
        "deep-work.a",
        "vi",
        "# Phần A\n\nXem [chương mô hình](ch03.html#sec_models) và [hình](#fig_query).",
    )

    assert written.structure_issues == []


def test_translation_may_carry_the_sections_figures(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", text="Body."))
    images = workspace.sources_parsed / "deep-work" / "images"
    images.mkdir(parents=True)
    (images / "fig1.png").write_bytes(b"png")

    written = service.write_section_translation(
        workspace,
        "deep-work",
        "deep-work.a",
        "vi",
        "Nội dung.\n\n![Hình 1](fig1.png)\n\n![Hình 2](missing.png)\n",
        includes_assets=True,
    )

    assert [(i.kind, i.target, i.change) for i in written.structure_issues] == [
        ("image", "missing.png", "added")
    ]


def test_structure_check_ignores_frontmatter_like_the_export(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", text=LINKED))

    written = service.write_section_translation(
        workspace,
        "deep-work",
        "deep-work.a",
        "vi",
        "---\nsource: <https://example.com/book/ch01.html>\n---\n"
        "Xem [chương](ch03.html#sec_models) và [hình](#fig_query).",
    )

    assert written.structure_issues == []


def test_structure_check_rebuilds_parsed_code_blocks(tmp_path: Path) -> None:
    workspace = WorkspacePaths(tmp_path)
    blocks = [
        CanonicalBlock(id="b0", type="title", text="A", level=1),
        CanonicalBlock(id="b1", type="text", text="See [x](a.html)."),
        CanonicalBlock(id="b2", type="text", text='<div id="app"></div>', metadata={"code": True}),
    ]
    write_document(
        Document(doc_id="deep-work", title="Deep Work", blocks=blocks),
        workspace.sources_parsed / "deep-work",
    )
    text = 'See [x](a.html).\n\n<div id="app"></div>'
    write_sections(
        [_section("deep-work.a", text=text, block_ids=["b0", "b1", "b2"])],
        workspace.sources_sections / "deep-work",
    )

    written = service.write_section_translation(
        workspace,
        "deep-work",
        "deep-work.a",
        "vi",
        'Xem [x](a.html).\n\n```html\n<div id="app"></div>\n```\n',
    )

    assert written.structure_issues == []


def test_absolute_image_path_is_never_a_carried_figure(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a", text="Body."))
    images = workspace.sources_parsed / "deep-work" / "images"
    images.mkdir(parents=True)
    (images / "fig1.png").write_bytes(b"png")
    absolute = (images / "fig1.png").resolve()

    written = service.write_section_translation(
        workspace,
        "deep-work",
        "deep-work.a",
        "vi",
        f"Nội dung.\n\n![Hình 1](fig1.png)\n\n![Hình 1]({absolute})\n",
        includes_assets=True,
    )

    # The relative path resolves and is allowed; the absolute one, though it points
    # at the same workspace file, is an absolute asset link and is reported.
    assert [(i.kind, i.target, i.change) for i in written.structure_issues] == [
        ("image", str(absolute), "added")
    ]


def test_notes_are_stored_beside_the_translation_not_in_it(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, _section("deep-work.a"))
    notes = "QA: ✅ glossary checked; kept 'deep work' untranslated.\nMEDIA:/tmp/review.pdf"

    service.write_section_translation(
        workspace, "deep-work", "deep-work.a", "vi", "# Bản dịch\n", notes=notes
    )
    view = service.get_section_translation(workspace, "deep-work", "deep-work.a", "vi")

    assert view.status == "fresh"
    assert view.notes == notes
    assert view.content == "# Bản dịch\n"
    assert view.metadata_path is not None and "glossary checked" in Path(
        view.metadata_path
    ).read_text(encoding="utf-8")
    listed = service.list_section_artifacts(workspace, "deep-work", "vi").artifacts
    assert [entry.notes for entry in listed] == [notes]
