"""Section ids double as filenames, so a long heading must not overflow NAME_MAX.

MinerU's ``flash`` tier can classify a code or TOC line as a title, which makes
300-character headings a real input for the heading segmenter.
"""

from __future__ import annotations

from pathlib import Path

from bookgraph.models import CanonicalBlock, Document
from bookgraph.sections import write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.utils import MAX_SECTION_SLUG_CHARS, unique_slug

LONG_CODE_TITLE = (
    'Table table = catalog.loadTable("myTable"); '
    + 'SparkActions.get().rewriteDataFiles(table).option("target-file-size-bytes", 512) ' * 4
    + ".execute();"
)


def test_unique_slug_caps_long_values_on_a_hyphen_boundary() -> None:
    slug = unique_slug(LONG_CODE_TITLE, set())

    assert len(LONG_CODE_TITLE) > 300
    assert len(slug) <= MAX_SECTION_SLUG_CHARS
    assert not slug.endswith("-")
    assert slug.startswith("table-table-catalog-loadtable-mytable-sparkactions")


def test_capped_slugs_stay_unique() -> None:
    used: set[str] = set()

    first = unique_slug(LONG_CODE_TITLE, used)
    second = unique_slug(LONG_CODE_TITLE + " again", used)

    assert first != second
    assert second == f"{first}-2"


def test_short_slugs_are_unchanged() -> None:
    assert unique_slug("Chapter 1 Replication", set()) == "chapter-1-replication"


def test_heading_segmenter_writes_sections_for_a_300_char_title(tmp_path: Path) -> None:
    document = Document(
        doc_id="apache-iceberg-tdg-er1",
        title="Iceberg",
        blocks=[
            CanonicalBlock(id="p0.b0", type="title", level=2, text="Chapter 1", order=0),
            CanonicalBlock(id="p1.b1", type="title", level=2, text=LONG_CODE_TITLE, order=1),
            CanonicalBlock(id="p1.b2", type="text", text="body", order=2),
        ],
    )
    sections = HeadingSegmenter(target_level=2).segment(document)

    output = write_sections(sections, tmp_path / "sections" / document.doc_id)

    assert len(sections) == 2
    for section in sections:
        assert (tmp_path / "sections" / document.doc_id / f"{section.id}.md").is_file()
    assert output is not None
