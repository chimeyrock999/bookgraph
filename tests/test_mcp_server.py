from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastmcp", reason="requires the optional 'mcp' extra")

from bookgraph.mcp.server import build_server  # noqa: E402
from bookgraph.models import ReadingPlan, Section  # noqa: E402
from bookgraph.reading_plans import write_reading_plan  # noqa: E402
from bookgraph.sections import write_sections  # noqa: E402
from bookgraph.workspace import WorkspacePaths  # noqa: E402


def _workspace(tmp_path: Path) -> WorkspacePaths:
    workspace = WorkspacePaths(tmp_path)
    write_sections(
        [
            Section(
                id="deep-work.a",
                doc_id="deep-work",
                title="Alpha",
                level=1,
                heading_path=["Alpha"],
                text="hello world",
            )
        ],
        workspace.sources_sections / "deep-work",
    )
    write_reading_plan(
        ReadingPlan(
            plan_id="daily",
            doc_id="deep-work",
            daily_sections=1,
            section_ids=["deep-work.a"],
        ),
        workspace.reading_plans_root / "daily.json",
    )
    return workspace


def test_build_server_registers_the_reading_and_query_tools(tmp_path: Path) -> None:
    server = build_server(_workspace(tmp_path))

    tools = asyncio.run(server.list_tools())

    assert server.name == "bookgraph"
    assert sorted(tool.name for tool in tools) == [
        "annotate_section",
        "complete_reading_batch",
        "create_plan",
        "get_concept",
        "get_context",
        "get_next_section",
        "get_outline",
        "get_related",
        "get_section",
        "list_documents",
        "list_plans",
        "mark_read",
        "search",
        "validate_reading_batch",
    ]


def test_get_next_section_tool_returns_section_content(tmp_path: Path) -> None:
    server = build_server(_workspace(tmp_path))

    result = asyncio.run(server.call_tool("get_next_section", {"plan_id": "daily"}))

    payload = result.structured_content
    assert payload["doc_id"] == "deep-work"
    assert [section["id"] for section in payload["sections"]] == ["deep-work.a"]
    assert payload["sections"][0]["text"] == "hello world"


def test_complete_reading_batch_tool_blocks_then_commits(tmp_path: Path) -> None:
    server = build_server(_workspace(tmp_path))

    blocked = asyncio.run(server.call_tool("complete_reading_batch", {"plan_id": "daily"}))
    payload = blocked.structured_content
    assert payload["committed"] is False
    assert [issue["code"] for issue in payload["issues"]] == [
        "index_missing",
        "annotation_missing",
    ]

    asyncio.run(
        server.call_tool(
            "annotate_section",
            {"doc_id": "deep-work", "section_id": "deep-work.a", "summary": "Hello."},
        )
    )
    checked = asyncio.run(
        server.call_tool("validate_reading_batch", {"plan_id": "daily", "index": "deferred"})
    )
    assert checked.structured_content["ok"] is True
    assert checked.structured_content["committed"] is False

    done = asyncio.run(
        server.call_tool("complete_reading_batch", {"plan_id": "daily", "index": "deferred"})
    )
    assert done.structured_content["committed"] is True
    assert done.structured_content["done"] is True
    assert done.structured_content["index_rebuild_needed"] is True


def test_search_tool_ranks_sections(tmp_path: Path) -> None:
    server = build_server(_workspace(tmp_path))

    result = asyncio.run(server.call_tool("search", {"query": "hello"}))

    hits = result.structured_content["hits"]
    assert [hit["section_id"] for hit in hits] == ["deep-work.a"]


def test_get_context_tool_returns_section_with_neighbourhood(tmp_path: Path) -> None:
    server = build_server(_workspace(tmp_path))

    result = asyncio.run(
        server.call_tool("get_context", {"doc_id": "deep-work", "section_id": "deep-work.a"})
    )

    payload = result.structured_content
    assert payload["section"]["text"] == "hello world"
    assert payload["related"]["section_id"] == "deep-work.a"
    assert payload["related"]["parent"] is None
