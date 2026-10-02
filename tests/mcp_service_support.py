"""Shared fixture helpers for the MCP reading-service tests."""

from __future__ import annotations

from pathlib import Path

from bookgraph.index.sqlite import SqliteIndexBackend
from bookgraph.models import ReadingPlan, Section
from bookgraph.reading_plans import write_reading_plan
from bookgraph.sections import read_sections, write_sections
from bookgraph.workspace import WorkspacePaths


def _build_index(workspace: WorkspacePaths, doc_id: str) -> None:
    """Index a segmented document exactly as ``bookgraph index build`` would."""

    sections = read_sections(workspace.sources_sections / doc_id / "sections.jsonl")
    SqliteIndexBackend().build_document(workspace, doc_id, doc_id, sections)


def _section(section_id: str, title: str, text: str = "Body.") -> Section:
    return Section(
        id=section_id,
        doc_id="deep-work",
        title=title,
        level=1,
        heading_path=[title],
        text=text,
    )


def _workspace(tmp_path: Path, *sections: Section) -> WorkspacePaths:
    workspace = WorkspacePaths(tmp_path)
    if sections:
        write_sections(list(sections), workspace.sources_sections / "deep-work")
    return workspace


def _plan(workspace: WorkspacePaths, *section_ids: str, completed: list[str] | None = None) -> None:
    plan = ReadingPlan(
        plan_id="daily",
        doc_id="deep-work",
        daily_sections=2,
        section_ids=list(section_ids),
        completed=completed or [],
    )
    write_reading_plan(plan, workspace.reading_plans_root / "daily.json")
