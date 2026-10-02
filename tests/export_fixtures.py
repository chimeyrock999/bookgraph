"""Shared fixture data for the translated/bilingual export tests.

A three-section book ("Tiny Book") parsed into blocks with one embedded figure, one
missing table asset and one equation, segmented by heading at level 2.
"""

from __future__ import annotations

import base64
from pathlib import Path

from bookgraph.documents import write_document
from bookgraph.exports.models import ExportReport
from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.sections import read_sections, write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.translations import write_translation
from bookgraph.workspace import WorkspacePaths

# 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
GENERATED_AT = "2026-10-02T00:00:00Z"
DOC = "tiny"


def tiny_blocks() -> list[CanonicalBlock]:
    return [
        CanonicalBlock(id="b0", type="title", text="Chapter One", level=1, page_idx=0),
        CanonicalBlock(id="b1", type="text", text="Intro prose in English.", page_idx=0),
        CanonicalBlock(
            id="b2",
            type="image",
            text="Figure 1. A diagram.",
            asset_path="fig1.png",
            page_idx=0,
        ),
        CanonicalBlock(id="b3", type="text", text="Prose after the figure.", page_idx=0),
        CanonicalBlock(id="b4", type="title", text="Section Two", level=2, page_idx=1),
        CanonicalBlock(id="b5", type="text", text="Second section English text.", page_idx=1),
        CanonicalBlock(
            id="b6",
            type="table",
            text="Table 1. Lost table.",
            asset_path="missing-table.png",
            page_idx=1,
        ),
        CanonicalBlock(id="b7", type="title", text="Section Three", level=2, page_idx=2),
        CanonicalBlock(id="b8", type="equation", text="E = mc^2", page_idx=2),
        CanonicalBlock(id="b9", type="text", text="Third section English text.", page_idx=2),
    ]


def write_tiny_book(paths: WorkspacePaths, blocks: list[CanonicalBlock] | None = None) -> None:
    """Write the parsed document and its sections manifest (re-segmenting it)."""

    document = Document(doc_id=DOC, title="Tiny Book", blocks=blocks or tiny_blocks())
    write_document(document, paths.sources_parsed / DOC)
    sections = HeadingSegmenter(target_level=2).segment(document)
    write_sections(sections, paths.sources_sections / DOC)


def make_workspace(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths(tmp_path)
    write_tiny_book(paths)
    (paths.sources_parsed / DOC / "images").mkdir()
    (paths.sources_parsed / DOC / "images" / "fig1.png").write_bytes(PNG)
    return paths


def section_ids(paths: WorkspacePaths) -> list[str]:
    return [section.id for section in sections(paths)]


def sections(paths: WorkspacePaths) -> list[Section]:
    return read_sections(paths.sources_sections / DOC / "sections.jsonl")


def register(
    paths: WorkspacePaths, section_id: str, body: str, *, includes_assets: bool = True
) -> None:
    """Write a translation through the registry: ``fresh`` against the current section."""

    section = next(s for s in sections(paths) if s.id == section_id)
    write_translation(paths, section, "vi", body, includes_assets=includes_assets)


def codes(report: ExportReport, section_id: str) -> list[str]:
    return [w.code for w in report.warnings if w.section_id == section_id]
