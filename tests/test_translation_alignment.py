"""Block-aligned translations: the MCP write path, its validation, and alignment status."""

from __future__ import annotations

import json

import pytest

from bookgraph.exports.translated import _Assembler
from bookgraph.mcp import reading_batch, service
from bookgraph.mcp.reading_batch import BatchRequirements
from bookgraph.mcp.service import ReadingServiceError
from bookgraph.models import AlignedUnit, Section, TranslationUnit
from bookgraph.sections import write_sections
from bookgraph.translation_alignment import check_alignment, join_units, markdown_parser
from bookgraph.workspace import WorkspacePaths
from translated_export_support import DOC, _blocks, _sections

CHAPTER = "tiny.chapter-one"


def _unit(*block_ids: str, content: str) -> TranslationUnit:
    return TranslationUnit(source_block_ids=list(block_ids), content=content)


def _write(workspace: WorkspacePaths, *units: TranslationUnit, section: str = CHAPTER):
    return service.write_section_translation(workspace, DOC, section, "vi", units=list(units))


_ONE_TO_ONE = (
    _unit("b0", content="# Chương Một"),
    _unit("b1", content="Phần mở đầu."),
    _unit("b2", content="![Hình 1. Sơ đồ.](images/fig1.png)"),
    _unit("b3", content="Văn bản sau hình."),
)


def test_units_join_into_the_body_and_record_spans(workspace: WorkspacePaths) -> None:
    written = _write(workspace, *_ONE_TO_ONE)

    assert written.status == "fresh"
    assert written.alignment_status == "aligned"
    assert written.aligned_units == 4
    assert written.alignment_issues == []
    view = service.get_section_translation(workspace, DOC, CHAPTER, "vi")
    assert view.content == (
        "# Chương Một\n\nPhần mở đầu.\n\n![Hình 1. Sơ đồ.](images/fig1.png)\n\nVăn bản sau hình.\n"
    )
    sidecar = json.loads((workspace.translations_root / "vi" / DOC / f"{CHAPTER}.json").read_text())
    spans = [
        (u["source_block_ids"], view.content[u["start"] : u["end"]]) for u in sidecar["alignment"]
    ]
    assert spans == [
        (["b0"], "# Chương Một"),
        (["b1"], "Phần mở đầu."),
        (["b2"], "![Hình 1. Sơ đồ.](images/fig1.png)"),
        (["b3"], "Văn bản sau hình."),
    ]


def test_merged_and_split_paragraphs_are_aligned(workspace: WorkspacePaths) -> None:
    merged = _write(
        workspace,
        _unit("b0", "b1", content="# Chương Một\n\nPhần mở đầu."),
        _unit("b2", "b3", content="![Hình 1.](images/fig1.png)\n\nVăn bản sau hình."),
    )
    assert (merged.alignment_status, merged.aligned_units) == ("aligned", 2)

    split = _write(
        workspace,
        _unit("b0", content="# Chương Một"),
        _unit("b1", content="Nửa đầu."),
        _unit("b1", content="Nửa sau."),
        _unit("b3", content="Văn bản sau hình."),
    )
    # The figure is passed through untranslated: not a gap.
    assert (split.alignment_status, split.aligned_units) == ("aligned", 4)
    assert split.alignment_issues == []


def test_a_missing_text_block_is_reported_not_refused(workspace: WorkspacePaths) -> None:
    written = _write(workspace, _unit("b0", content="# Chương Một"), _unit("b1", content="Mở đầu."))

    assert written.alignment_status == "aligned"
    assert [(i.code, i.block_id) for i in written.alignment_issues] == [("unaligned_block", "b3")]


@pytest.mark.parametrize(
    ("units", "fragment"),
    [
        ((_unit("b1", content="Mở đầu."), _unit("b5", content="Khác.")), "b5, which is not in"),
        ((_unit("b3", content="Sau."), _unit("b1", content="Trước.")), "b1 out of source order"),
        ((_unit("b3", "b1", content="Ngược."),), "b1 out of source order"),
        ((_unit("b1", content="  "),), "needs content"),
        ((_unit(content="Không khối."),), "needs content"),
    ],
    ids=["foreign", "out-of-order-units", "out-of-order-ids", "empty-content", "no-ids"],
)
def test_misaligned_units_are_refused(
    workspace: WorkspacePaths, units: tuple[TranslationUnit, ...], fragment: str
) -> None:
    with pytest.raises(ReadingServiceError, match="do not align") as error:
        _write(workspace, *units)

    assert fragment in str(error.value)
    view = service.get_section_translation(workspace, DOC, CHAPTER, "vi")
    assert view.status == "missing"


def test_content_and_units_are_exclusive(workspace: WorkspacePaths) -> None:
    with pytest.raises(ReadingServiceError, match="either content or units"):
        service.write_section_translation(
            workspace, DOC, CHAPTER, "vi", "# Chương", units=list(_ONE_TO_ONE)
        )
    with pytest.raises(ReadingServiceError, match="units must not be empty"):
        service.write_section_translation(workspace, DOC, CHAPTER, "vi", units=[])


def test_plain_content_stays_valid_and_unaligned(workspace: WorkspacePaths) -> None:
    written = service.write_section_translation(workspace, DOC, CHAPTER, "vi", "# Chương Một")

    assert written.status == "fresh"
    assert (written.alignment_status, written.aligned_units) == ("unaligned", 0)
    missing = service.get_section_translation(workspace, DOC, "tiny.section-two", "vi")
    assert missing.alignment_status is None


def test_an_edited_body_drops_its_alignment(workspace: WorkspacePaths) -> None:
    _write(workspace, *_ONE_TO_ONE)
    body = workspace.translations_root / "vi" / DOC / f"{CHAPTER}.md"
    body.write_text(body.read_text() + "\nThêm.\n", encoding="utf-8")

    view = service.get_section_translation(workspace, DOC, CHAPTER, "vi")
    assert (view.status, view.alignment_status) == ("untracked", "unaligned")


def test_a_resegment_that_moves_blocks_makes_the_alignment_invalid(
    workspace: WorkspacePaths,
) -> None:
    _write(workspace, *_ONE_TO_ONE)
    # Same words (so the translation stays fresh), different block ids.
    sections = [
        s.model_copy(update={"block_ids": [f"x{b}" for b in s.block_ids]}) if s.id == CHAPTER else s
        for s in _sections(workspace)
    ]
    write_sections(sections, workspace.sources_sections / DOC)

    view = service.get_section_translation(workspace, DOC, CHAPTER, "vi")
    assert view.status == "fresh"
    assert (view.alignment_status, view.aligned_units) == ("invalid", 0)
    assert {issue.code for issue in view.alignment_issues} == {"foreign_block"}


def test_section_blocks_are_listed_on_request(workspace: WorkspacePaths) -> None:
    assert service.get_section(workspace, DOC, CHAPTER).blocks is None

    view = service.get_section(workspace, DOC, CHAPTER, include_blocks=True)
    assert view.blocks is not None
    assert [(b.id, b.type, b.text) for b in view.blocks] == [
        (b.id, b.type, b.text) for b in _blocks()[:4]
    ]


def test_batch_completion_reports_alignment_without_blocking(workspace: WorkspacePaths) -> None:
    service.create_plan(workspace, DOC)
    _write(workspace, _unit("b0", content="# Chương Một"), _unit("b1", content="Mở đầu."))
    requirements = BatchRequirements(
        require_annotation=False,
        index="ignore",
        require_assets=False,
        translation_lang="vi",
    )

    report = reading_batch.complete_reading_batch(workspace, DOC, [CHAPTER], requirements)

    assert report.committed
    gaps = [issue for issue in report.issues if issue.code == "translation_alignment_gaps"]
    assert len(gaps) == 1 and not gaps[0].blocking
    assert "b3" in gaps[0].message


def test_check_alignment_flags_spans_that_do_not_tile_the_body() -> None:
    section = Section(
        id="s",
        doc_id="d",
        title="S",
        level=1,
        heading_path=["S"],
        text="A\n\nB",
        block_ids=["a", "b"],
    )
    body, units = join_units([_unit("a", content="Một"), _unit("b", content="Hai")])
    assert check_alignment(section, {}, units, body) == []

    shifted = [units[0], AlignedUnit(source_block_ids=["b"], start=0, end=3)]
    assert [i.code for i in check_alignment(section, {}, shifted, body)] == [
        "bad_range",
        "bad_range",
    ]


def test_a_unit_of_only_reference_definitions_is_reported_as_merged(
    workspace: WorkspacePaths,
) -> None:
    written = _write(
        workspace,
        _unit("b0", "b1", content="# Chương Một\n\nMở đầu [x]."),
        _unit("b3", content="[x]: https://example.com"),
    )

    (issue,) = written.alignment_issues
    assert (issue.code, issue.unit) == ("unit_not_a_block", 1)
    assert "renders no block" in issue.message


def test_the_export_renders_in_the_alignment_check_dialect() -> None:
    assert _Assembler.__dataclass_fields__["_md"].default_factory is markdown_parser
