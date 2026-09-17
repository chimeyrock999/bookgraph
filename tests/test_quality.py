from __future__ import annotations

from pathlib import Path

from bookgraph.models import CanonicalBlock, Section
from bookgraph.quality import (
    AssetSummary,
    asset_summaries,
    classify_asset,
    document_quality_report,
    read_quality_report,
    section_warnings,
    write_quality_report,
)


def _section(
    section_id: str = "iceberg.a",
    *,
    text: str = "A paragraph of genuine prose about the chapter, long enough to be real.",
    page_start: int | None = 10,
    page_end: int | None = 12,
    block_ids: list[str] | None = None,
) -> Section:
    return Section(
        id=section_id,
        doc_id="iceberg",
        title="Alpha",
        level=1,
        heading_path=["Alpha"],
        page_start=page_start,
        page_end=page_end,
        text=text,
        block_ids=block_ids or [],
    )


def test_clean_section_has_no_warnings() -> None:
    assert section_warnings(_section()) == []


def test_inverted_page_range_is_reported() -> None:
    warnings = section_warnings(_section(page_start=12, page_end=4))

    assert [warning.code for warning in warnings] == ["page_range_inverted"]
    assert "page_start=12 > page_end=4" in warnings[0].message


def test_half_known_page_range_is_reported() -> None:
    warnings = section_warnings(_section(page_start=None, page_end=7))

    assert [warning.code for warning in warnings] == ["page_range_incomplete"]


def test_missing_page_range_is_not_a_warning() -> None:
    # A document without page provenance at all (Markdown, EPUB) is normal, not broken.
    assert section_warnings(_section(page_start=None, page_end=None)) == []


def test_classify_asset_corroborates_caption_label() -> None:
    figure = classify_asset("image", "Figure 3-1. The commit protocol.")
    chart = classify_asset("chart", "Figure 4. Throughput over time.")
    table = classify_asset("table", "Table 2: Supported operations")

    assert (figure.confidence, figure.suggested_type) == (1.0, None)
    assert (chart.confidence, chart.suggested_type) == (1.0, None)
    assert (table.confidence, table.suggested_type) == (1.0, None)


def test_classify_asset_without_a_label_is_uncorroborated() -> None:
    classification = classify_asset("table", "Supported operations")

    assert classification.confidence == 0.8
    assert classification.suggested_type is None
    assert classification.type == "table"


def test_classify_asset_flags_a_figure_parsed_as_a_table() -> None:
    # The dogfooded case: MinerU emitted a figure as a ``table`` block (issue #38).
    classification = classify_asset("table", "**Figure 6.** Snapshot isolation.")

    assert classification.confidence == 0.4
    assert classification.suggested_type == "image"
    assert classification.type == "table"  # the parser's verdict is preserved, not rewritten


def test_a_passing_mention_of_a_table_is_not_a_caption_label() -> None:
    # Anchored matching: only a leading label counts, so prose cannot dispute a type.
    classification = classify_asset("image", "The layout shown here, as the table above lists")

    assert classification.suggested_type is None


def test_ambiguous_asset_type_is_reported_per_block() -> None:
    warnings = section_warnings(
        _section(),
        [AssetSummary(block_id="p3.b2", type="table", caption="Figure 6. Snapshot isolation.")],
    )

    assert [warning.code for warning in warnings] == ["asset_type_ambiguous"]
    assert warnings[0].block_id == "p3.b2"
    assert "'image'" in warnings[0].message


def test_caption_only_prose_is_reported() -> None:
    warnings = section_warnings(
        _section(text="Figure 1. Pipeline. Table 1. Results."),
        [
            AssetSummary(block_id="img1", type="image", caption="Figure 1. Pipeline."),
            AssetSummary(block_id="tbl1", type="table", caption="Table 1. Results."),
        ],
    )

    assert [warning.code for warning in warnings] == ["asset_captions_only"]


def test_sparse_prose_around_several_assets_is_reported() -> None:
    warnings = section_warnings(
        _section(text="Figure 1. Pipeline. Table 1. Results. Both are discussed below."),
        [
            AssetSummary(block_id="img1", type="image", caption="Figure 1. Pipeline."),
            AssetSummary(block_id="tbl1", type="table", caption="Table 1. Results."),
        ],
    )

    assert [warning.code for warning in warnings] == ["asset_text_sparse"]
    assert "25 characters of prose" in warnings[0].message


def test_a_single_figure_beside_short_prose_is_not_sparse() -> None:
    # One figure next to a short but genuine paragraph is ordinary book prose.
    warnings = section_warnings(
        _section(text="See the diagram for how the ingest pipeline is wired."),
        [AssetSummary(block_id="img1", type="image", caption="Pipeline")],
    )

    assert warnings == []


def test_document_report_aggregates_per_section_warnings(tmp_path: Path) -> None:
    sections = [
        _section("iceberg.a", page_start=12, page_end=4),
        _section(
            "iceberg.b",
            text="Figure 6. Snapshot isolation.",
            block_ids=["img1", "inline_tbl"],
        ),
        _section("iceberg.c"),
    ]
    blocks = [
        CanonicalBlock(
            id="img1", type="table", text="Figure 6. Snapshot isolation.", asset_path="f6.jpg"
        ),
        # A Markdown table rendered inline into the section text carries no asset file, so
        # it must not be counted as a figure whose content lives outside ``text``.
        CanonicalBlock(id="inline_tbl", type="table", text="| a | b |"),
    ]

    report = document_quality_report("iceberg", sections, blocks)

    assert report.doc_id == "iceberg"
    assert report.section_count == 3
    assert report.warning_counts == {
        "asset_captions_only": 1,
        "asset_type_ambiguous": 1,
        "page_range_inverted": 1,
    }
    assert report.warning_count == 3
    assert {warning.section_id for warning in report.warnings} == {"iceberg.a", "iceberg.b"}

    path = write_quality_report(report, tmp_path)
    assert path.name == "quality.json"
    assert read_quality_report(path) == report
    # Deterministic: same input, byte-identical report (no timestamps).
    before = path.read_bytes()
    write_quality_report(document_quality_report("iceberg", sections, blocks), tmp_path)
    assert path.read_bytes() == before


def test_document_report_is_empty_for_a_clean_document() -> None:
    report = document_quality_report("iceberg", [_section()])

    assert (report.warning_count, report.warnings, report.warning_counts) == (0, [], {})


def test_classify_asset_flags_a_table_parsed_as_a_chart() -> None:
    # A chart block is a figure as far as caption labels go, so a table caption on one
    # is a genuine conflict — the branch that keeps `chart` out of the table label set.
    conflicting = classify_asset("chart", "Table 5: latency percentiles")
    agreeing = classify_asset("chart", "Figure 5. Latency over time.")

    assert (conflicting.confidence, conflicting.suggested_type) == (0.4, "table")
    assert (agreeing.confidence, agreeing.suggested_type) == (1.0, None)


def test_captions_are_stripped_longest_first() -> None:
    # "Table" is a substring of "Table 2 data": stripping the short caption first would
    # eat the head of the long one, leave "2 data" behind, and hide a caption-only section.
    warnings = section_warnings(
        _section(text="Table Table 2 data"),
        [
            AssetSummary(block_id="short", type="table", caption="Table"),
            AssetSummary(block_id="long", type="table", caption="Table 2 data"),
        ],
    )

    assert [warning.code for warning in warnings] == ["asset_captions_only"]


def test_unstaged_asset_file_is_reported_instead_of_a_type_dispute() -> None:
    warnings = section_warnings(
        _section(text="Figure 6. Snapshot isolation."),
        [
            AssetSummary(
                block_id="img1",
                type="table",  # the caption disputes this, but the file cannot settle it
                caption="Figure 6. Snapshot isolation.",
                resolved=False,
            )
        ],
    )

    assert [warning.code for warning in warnings] == [
        "asset_file_missing",
        "asset_captions_only",
    ]
    assert warnings[0].block_id == "img1"


def test_asset_summaries_resolve_against_the_parsed_directory(tmp_path: Path) -> None:
    staged = CanonicalBlock(id="ok", type="image", text="Figure 1.", asset_path="f1.jpg")
    unstaged = CanonicalBlock(id="gone", type="image", text="Figure 2.", asset_path="f2.jpg")
    inline = CanonicalBlock(id="inline", type="table", text="| a | b |")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "f1.jpg").write_bytes(b"\xff\xd8\xff")

    summaries = asset_summaries([staged, unstaged, inline], tmp_path)

    assert set(summaries) == {"ok", "gone"}  # the inline table is content, not an asset
    assert summaries["ok"].resolved is True
    assert summaries["gone"].resolved is False
    # Without a parsed dir there is nothing to check against, so references are taken as is.
    assert asset_summaries([unstaged])["gone"].resolved is True
