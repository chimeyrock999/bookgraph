"""Shared fixture data and helpers for the translated-export tests."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from bookgraph.exports.models import ExportReport
from bookgraph.models import CanonicalBlock, Section
from bookgraph.sections import read_sections, write_sections
from bookgraph.translations import write_translation
from bookgraph.workspace import WorkspacePaths

# 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
GENERATED_AT = "2026-10-02T00:00:00Z"
DOC = "tiny"


def _blocks() -> list[CanonicalBlock]:
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


def _section_ids(paths: WorkspacePaths) -> list[str]:
    manifest = paths.sources_sections / DOC / "sections.jsonl"
    return [json.loads(line)["id"] for line in manifest.read_text().splitlines()]


def _translate(paths: WorkspacePaths, section_id: str, body: str) -> Path:
    """Drop a body at the registry path with no sidecar: an ``untracked`` translation."""

    path = paths.translations_root / "vi" / DOC / f"{section_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _sections(paths: WorkspacePaths) -> list[Section]:
    return read_sections(paths.sources_sections / DOC / "sections.jsonl")


def _register(
    paths: WorkspacePaths, section_id: str, body: str, *, includes_assets: bool = True
) -> None:
    """Write a translation through the registry: ``fresh`` against the current section."""

    section = next(s for s in _sections(paths) if s.id == section_id)
    write_translation(paths, section, "vi", body, includes_assets=includes_assets)


def _change_section_text(paths: WorkspacePaths, section_id: str) -> None:
    """Simulate a re-segment that changed the section's words, making it stale."""

    sections = [
        s.model_copy(update={"text": s.text + " Revised."}) if s.id == section_id else s
        for s in _sections(paths)
    ]
    write_sections(sections, paths.sources_sections / DOC)


def _codes(report: ExportReport, section_id: str) -> list[str]:
    return [w.code for w in report.warnings if w.section_id == section_id]
