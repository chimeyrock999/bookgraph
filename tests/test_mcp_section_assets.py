"""Section assets from parsed blocks: ``AssetRef``s, asset warnings, the block cache."""

from __future__ import annotations

import os
from pathlib import Path

from bookgraph.documents import write_document
from bookgraph.mcp import loading, service
from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.workspace import WorkspacePaths
from mcp_service_support import _plan, _section, _workspace


def _stage_asset(workspace: WorkspacePaths, doc_id: str, rel: str) -> Path:
    """Create a real asset file under sources/parsed/<doc_id>/ (as MinerU staging would)."""

    path = workspace.sources_parsed / doc_id / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff")  # minimal JPEG-ish bytes; content is irrelevant here
    return path


def _write_document_with_assets(workspace: WorkspacePaths, doc_id: str = "deep-work") -> None:
    """Write a parsed ``document.json`` and stage the image/table files it references."""

    _stage_asset(workspace, doc_id, "images/fig1.jpg")
    _stage_asset(workspace, doc_id, "nested/tbl1.jpg")
    document = Document(
        doc_id=doc_id,
        title="Deep Work",
        blocks=[
            CanonicalBlock(id="t0", type="title", text="Figures", order=0, page_idx=1),
            CanonicalBlock(
                id="img1",
                type="image",
                text="Figure 1. The pipeline.",
                asset_path="fig1.jpg",
                order=1,
                page_idx=1,
            ),
            CanonicalBlock(
                id="tbl1",
                type="table",
                text="Table 1. Results.",
                asset_path="nested/tbl1.jpg",
                order=2,
                page_idx=2,
            ),
        ],
    )
    write_document(document, workspace.sources_parsed / doc_id)


def test_get_section_returns_structured_assets(tmp_path: Path) -> None:
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Figure 1. The pipeline. Table 1. Results.",
        block_ids=["t0", "img1", "tbl1"],
    )
    workspace = _workspace(tmp_path, section)
    _write_document_with_assets(workspace)

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert [asset.type for asset in view.assets] == ["image", "table"]
    image, table = view.assets
    assert image.block_id == "img1"
    assert image.caption == "Figure 1. The pipeline."
    assert image.order == 1
    assert image.page_idx == 1
    # A bare filename resolves under the staged ``images/`` dir; a relative path is kept.
    assert image.path == str(workspace.sources_parsed / "deep-work" / "images" / "fig1.jpg")
    assert table.path == str(workspace.sources_parsed / "deep-work" / "nested" / "tbl1.jpg")
    # Prose is effectively just the captions, so the reader is warned to open the assets.
    assert [warning.code for warning in view.warnings] == ["asset_captions_only"]


def test_get_section_include_assets_false_omits_assets(tmp_path: Path) -> None:
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Figure 1. The pipeline.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    _write_document_with_assets(workspace)

    view = service.get_section(workspace, "deep-work", "deep-work.figs", include_assets=False)

    assert view.assets == []
    assert view.warnings == []


def test_get_section_without_parsed_document_has_no_assets(tmp_path: Path) -> None:
    # The existing fixtures only write sections.jsonl (no document.json) — assets stay empty.
    workspace = _workspace(tmp_path, _section("deep-work.a", "Alpha"))

    view = service.get_section(workspace, "deep-work", "deep-work.a")

    assert view.assets == []
    assert view.warnings == []


def test_get_context_carries_section_assets(tmp_path: Path) -> None:
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Figure 1. The pipeline.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    _write_document_with_assets(workspace)

    context = service.get_context(workspace, "deep-work", "deep-work.figs")

    assert [asset.block_id for asset in context.section.assets] == ["img1"]


def _workspace_with_blocks(tmp_path: Path, *blocks: CanonicalBlock) -> WorkspacePaths:
    """A workspace whose single section owns exactly ``blocks`` (via ``document.json``)."""

    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Some genuine prose about the figures that is clearly longer than a caption.",
        block_ids=[block.id for block in blocks],
    )
    workspace = _workspace(tmp_path, section)
    write_document(
        Document(doc_id="deep-work", title="Deep Work", blocks=list(blocks)),
        workspace.sources_parsed / "deep-work",
    )
    return workspace


def test_assets_drop_unresolvable_references(tmp_path: Path) -> None:
    # URL, absolute path, workspace-escaping relative path, and a markdown table rendered
    # inline (no asset_path, no src) must NOT surface as AssetRefs — an AssetRef must always
    # point at a real workspace file.
    workspace = _workspace_with_blocks(
        tmp_path,
        CanonicalBlock(id="url", type="image", metadata={"src": "https://example.com/x.png"}),
        CanonicalBlock(id="abs", type="image", asset_path="/etc/passwd"),
        CanonicalBlock(id="escape", type="image", asset_path="../../secret.jpg"),
        CanonicalBlock(id="inline_tbl", type="table", text="| a | b |"),
        CanonicalBlock(id="ok", type="image", asset_path="real.jpg", order=9),
    )
    _stage_asset(workspace, "deep-work", "images/real.jpg")  # only the "ok" block has a file

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert [asset.block_id for asset in view.assets] == ["ok"]
    assert view.assets[0].path == str(
        workspace.sources_parsed / "deep-work" / "images" / "real.jpg"
    )


def test_asset_warning_not_raised_for_genuine_short_prose(tmp_path: Path) -> None:
    # A short but real sentence next to a one-word caption must not trip the caption-only
    # warning (regression for the length-only heuristic).
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="See the diagram for how the ingest pipeline is wired end to end.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[
                CanonicalBlock(id="img1", type="image", text="Pipeline", asset_path="p.jpg")
            ],
        ),
        workspace.sources_parsed / "deep-work",
    )
    _stage_asset(workspace, "deep-work", "images/p.jpg")

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets  # the image is still surfaced
    assert view.warnings == []  # but the prose is genuine, so no caption-only warning


def test_asset_dropped_when_referenced_file_is_missing(tmp_path: Path) -> None:
    # asset_path names a file the parser never staged — no AssetRef should be emitted.
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[CanonicalBlock(id="img1", type="image", asset_path="ghost.jpg")],
        ),
        workspace.sources_parsed / "deep-work",
    )

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets == []


def test_asset_dropped_for_symlink_escaping_the_workspace(tmp_path: Path) -> None:
    # A symlink under images/ that points outside the workspace must not resolve to a
    # returnable path — the containment check follows symlinks, not just the lexical path.
    outside = tmp_path / "outside_secret.jpg"
    outside.write_bytes(b"\xff\xd8\xff")
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    images_dir = workspace.sources_parsed / "deep-work" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "evil.jpg").symlink_to(outside)
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[CanonicalBlock(id="img1", type="image", asset_path="evil.jpg")],
        ),
        workspace.sources_parsed / "deep-work",
    )

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets == []


def test_doc_blocks_cache_invalidates_on_same_mtime_size_change(tmp_path: Path) -> None:
    loading._DOC_BLOCKS_CACHE.clear()
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    _stage_asset(workspace, "deep-work", "images/a.jpg")
    document_path = workspace.sources_parsed / "deep-work" / "document.json"

    def write(caption: str) -> None:
        write_document(
            Document(
                doc_id="deep-work",
                title="Deep Work",
                blocks=[
                    CanonicalBlock(id="img1", type="image", text=caption, asset_path="a.jpg")
                ],
            ),
            workspace.sources_parsed / "deep-work",
        )

    write("First caption.")
    first = service.get_section(workspace, "deep-work", "deep-work.figs")
    assert first.assets[0].caption == "First caption."
    mtime = document_path.stat().st_mtime

    # Rewrite with a different length, then force the *same* mtime: only the size differs.
    write("A second, noticeably longer caption than the first one.")
    os.utime(document_path, (mtime, mtime))

    second = service.get_section(workspace, "deep-work", "deep-work.figs")
    assert second.assets[0].caption == "A second, noticeably longer caption than the first one."


def test_doc_blocks_cache_is_bounded(tmp_path: Path) -> None:
    loading._DOC_BLOCKS_CACHE.clear()
    workspace = WorkspacePaths(tmp_path)
    for i in range(loading._DOC_BLOCKS_CACHE_MAX + 10):
        doc_id = f"doc{i}"
        write_document(
            Document(doc_id=doc_id, title=doc_id, blocks=[]),
            workspace.sources_parsed / doc_id,
        )
        loading._load_doc_blocks(workspace, doc_id)

    assert len(loading._DOC_BLOCKS_CACHE) <= loading._DOC_BLOCKS_CACHE_MAX


def test_asset_with_embedded_nul_degrades_instead_of_crashing(tmp_path: Path) -> None:
    # A corrupt/adversarial document.json can carry a NUL byte in a path; resolving it
    # raises ValueError, which must degrade to "no asset", not crash the section fetch.
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[CanonicalBlock(id="img1", type="image", asset_path="fig\x00.jpg")],
        ),
        workspace.sources_parsed / "deep-work",
    )

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets == []


def test_get_next_section_include_assets_toggle(tmp_path: Path) -> None:
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    _stage_asset(workspace, "deep-work", "images/fig1.jpg")
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[CanonicalBlock(id="img1", type="image", asset_path="fig1.jpg")],
        ),
        workspace.sources_parsed / "deep-work",
    )
    _plan(workspace, "deep-work.figs")

    with_assets = service.get_next_section(workspace, "daily")
    assert [a.block_id for a in with_assets.sections[0].assets] == ["img1"]

    without = service.get_next_section(workspace, "daily", include_assets=False)
    assert without.sections[0].assets == []


def test_asset_warning_not_raised_when_caption_token_recurs_in_prose(tmp_path: Path) -> None:
    # A short caption that also recurs in the body must not be wiped everywhere: removing it
    # once leaves genuine content, so no false "caption-only" warning (recurring-token bug).
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="node node node node done",  # caption "node" appears once as caption + recurs
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    _stage_asset(workspace, "deep-work", "images/n.jpg")
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[CanonicalBlock(id="img1", type="image", text="node", asset_path="n.jpg")],
        ),
        workspace.sources_parsed / "deep-work",
    )

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets
    assert view.warnings == []  # replace-all would falsely fire; replace-once does not


def test_asset_resolves_file_directly_under_parsed_root(tmp_path: Path) -> None:
    # A bare filename staged directly under the parsed dir (not images/) must still resolve
    # — the location is verified on disk, not guessed from whether the name contains a slash.
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Body.",
        block_ids=["img1"],
    )
    workspace = _workspace(tmp_path, section)
    _stage_asset(workspace, "deep-work", "cover.png")  # directly under parsed_root, no images/
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[CanonicalBlock(id="img1", type="image", asset_path="cover.png")],
        ),
        workspace.sources_parsed / "deep-work",
    )

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets[0].path == str(workspace.sources_parsed / "deep-work" / "cover.png")


def test_doc_blocks_cache_is_thread_safe(tmp_path: Path) -> None:
    import threading

    loading._DOC_BLOCKS_CACHE.clear()
    workspace = WorkspacePaths(tmp_path)
    doc_ids = [f"doc{i}" for i in range(loading._DOC_BLOCKS_CACHE_MAX + 20)]
    for doc_id in doc_ids:
        write_document(
            Document(doc_id=doc_id, title=doc_id, blocks=[]),
            workspace.sources_parsed / doc_id,
        )

    errors: list[Exception] = []

    def load(doc_id: str) -> None:
        try:
            for _ in range(5):
                loading._load_doc_blocks(workspace, doc_id)
        except Exception as exc:  # noqa: BLE001 - surfaced via the errors list below
            errors.append(exc)

    threads = [threading.Thread(target=load, args=(d,)) for d in doc_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(loading._DOC_BLOCKS_CACHE) <= loading._DOC_BLOCKS_CACHE_MAX


def test_section_assets_carry_type_confidence_and_a_suggested_correction(tmp_path: Path) -> None:
    # A figure the parser classified as a table: the type is preserved, but the caption
    # disputes it, so the client gets a confidence and the type the caption implies.
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text=(
            "A full paragraph of real prose about snapshot isolation, long enough that "
            "the section is clearly not just its captions and does not trip the sparse-"
            "text check, so the only warning left is the disputed asset type itself."
        ),
        block_ids=["mislabelled", "plain"],
    )
    workspace = _workspace(tmp_path, section)
    _stage_asset(workspace, "deep-work", "images/f6.jpg")
    _stage_asset(workspace, "deep-work", "images/t2.jpg")
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[
                CanonicalBlock(
                    id="mislabelled",
                    type="table",
                    text="Figure 6. Snapshot isolation.",
                    asset_path="f6.jpg",
                ),
                CanonicalBlock(
                    id="plain", type="table", text="Table 2. Operations.", asset_path="t2.jpg"
                ),
            ],
        ),
        workspace.sources_parsed / "deep-work",
    )

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    mislabelled, plain = view.assets
    assert (mislabelled.type, mislabelled.suggested_type) == ("table", "image")
    assert mislabelled.type_confidence == 0.4
    assert (plain.type, plain.suggested_type) == ("table", None)
    assert plain.type_confidence == 1.0
    assert [(w.code, w.block_id) for w in view.warnings] == [
        ("asset_type_ambiguous", "mislabelled")
    ]


def test_section_warns_about_an_asset_file_the_parser_never_staged(tmp_path: Path) -> None:
    # Ingest and the section APIs resolve assets through the same bookgraph.assets helper,
    # so a reference with no file on disk is reported here too instead of vanishing from
    # the response while quality.json counts it (review follow-up on #47).
    section = Section(
        id="deep-work.figs",
        doc_id="deep-work",
        title="Figures",
        level=1,
        heading_path=["Figures"],
        text="Figure 6. Snapshot isolation.",
        block_ids=["gone", "inline_tbl"],
    )
    workspace = _workspace(tmp_path, section)
    write_document(
        Document(
            doc_id="deep-work",
            title="Deep Work",
            blocks=[
                CanonicalBlock(
                    id="gone",
                    type="image",
                    text="Figure 6. Snapshot isolation.",
                    asset_path="never-staged.jpg",
                ),
                # No file reference at all: content, not a missing asset.
                CanonicalBlock(id="inline_tbl", type="table", text="| a | b |"),
            ],
        ),
        workspace.sources_parsed / "deep-work",
    )

    view = service.get_section(workspace, "deep-work", "deep-work.figs")

    assert view.assets == []  # an AssetRef.path must always open, so it is dropped
    assert [(w.code, w.block_id) for w in view.warnings] == [
        ("asset_file_missing", "gone"),
        ("asset_captions_only", None),
    ]
