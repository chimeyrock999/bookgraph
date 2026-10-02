"""Exports resolve links to the block anchors MinerU records (``metadata.anchor``).

MinerU rewrites an EPUB's internal links to ids it assigns (``#epub-<hash>``) and puts
each id on the block it marks. The workspace is built through the real pipeline from
the recorded MinerU 4 EPUB bundle (``mineru-middle-json`` → heading segmenter), so a
link must land on the section that holds the anchored block, in an original and in a
translation that keeps the destination as written.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from bookgraph.documents import write_document
from bookgraph.exports.models import INTERNAL_LINK_UNRESOLVED
from bookgraph.exports.translated import build_translated_export
from bookgraph.parsers.mineru import MinerUMiddleJsonParser
from bookgraph.sections import read_sections, write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.translations import write_translation
from bookgraph.workspace import WorkspacePaths

FIXTURES = Path(__file__).parent / "fixtures" / "mineru4" / "epub"
GENERATED_AT = "2026-10-03T00:00:00Z"
DOC = "sample"
TO_REPLICATION = "#epub-a652b1f1fec799e1a325"  # anchor on the "Replication" title
TO_INTRO = "#epub-60e9ef05336bc7d9323b"  # anchor on Chapter One's first paragraph


def _workspace(tmp_path: Path) -> tuple[WorkspacePaths, dict[str, str]]:
    paths = WorkspacePaths(tmp_path / "workspace")
    parsed_dir = paths.sources_parsed / DOC
    parsed_dir.mkdir(parents=True)
    shutil.copy(FIXTURES / "middle_json.json", parsed_dir / f"{DOC}_middle.json")
    shutil.copytree(FIXTURES / "images", parsed_dir / "images")
    document = MinerUMiddleJsonParser().parse(parsed_dir / f"{DOC}_middle.json", parsed_dir)
    write_document(document, parsed_dir)
    write_sections(HeadingSegmenter(target_level=2).segment(document), paths.sources_sections / DOC)
    sections = read_sections(paths.sources_sections / DOC / "sections.jsonl")
    return paths, {section.title: section.id for section in sections}


def _hrefs(html: str) -> list[str]:
    return re.findall(r'<a href="([^"]*)"', html.split("<main>", 1)[1])


def test_original_links_land_on_the_section_holding_the_anchor(tmp_path: Path) -> None:
    paths, ids = _workspace(tmp_path)

    export = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT)

    hrefs = _hrefs(export.html)
    assert f"#{ids['Replication']}" in hrefs
    assert f"#{ids['Chapter One']}" in hrefs
    assert TO_REPLICATION[1:] not in "".join(hrefs)
    assert INTERNAL_LINK_UNRESOLVED not in [w.code for w in export.report.warnings]


def test_translation_keeping_the_anchor_link_resolves_too(tmp_path: Path) -> None:
    paths, ids = _workspace(tmp_path)
    section = next(
        s
        for s in read_sections(paths.sources_sections / DOC / "sections.jsonl")
        if s.title == "Replication"
    )
    write_translation(paths, section, "vi", f"## Sao chép\n\nQuay lại [phần mở đầu]({TO_INTRO}).\n")

    export = build_translated_export(paths, DOC, lang="vi", generated_at=GENERATED_AT)

    assert f'<a href="#{ids["Chapter One"]}">phần mở đầu</a>' in export.html
    assert INTERNAL_LINK_UNRESOLVED not in [w.code for w in export.report.warnings]
