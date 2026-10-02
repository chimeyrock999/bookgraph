from __future__ import annotations

import json

import pytest

from bookgraph.exports.models import TRANSLATION_STALE, TRANSLATION_UNTRACKED, FallbackPolicy
from bookgraph.exports.translated import ExportError, build_translated_export
from bookgraph.sections import write_sections
from bookgraph.workspace import WorkspacePaths
from translated_export_support import (
    DOC,
    GENERATED_AT,
    _change_section_text,
    _register,
    _section_ids,
    _sections,
    _translate,
)


def test_duplicate_section_ids_are_an_export_error(workspace: WorkspacePaths) -> None:
    manifest = workspace.sources_sections / DOC / "sections.jsonl"
    first = manifest.read_text().splitlines()[0]
    manifest.write_text(manifest.read_text() + first + "\n")

    with pytest.raises(ExportError, match="duplicate section ids: tiny.chapter-one"):
        build_translated_export(workspace, DOC, lang="vi")


def _write_outline(paths: WorkspacePaths, bookmarks: list[tuple[str, int, int]]) -> None:
    manifest = paths.sources_inbox / DOC / "book.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "pdf": {
                    "bookmarks": [
                        {"title": title, "page_index": page, "level": level}
                        for title, page, level in bookmarks
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def test_child_sections_render_inside_their_chapter(workspace: WorkspacePaths) -> None:
    chapter, second, third = _section_ids(workspace)
    _register(workspace, second, "# Phần Hai\n\nNội dung.\n")

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)
    html = export.html

    assert [(e.depth, e.parent_id) for e in export.report.sections] == [
        (1, None),
        (2, chapter),
        (2, chapter),
    ]
    # One chapter <section> holding both child sections; only the chapter opens a page.
    main = html[html.index("<main>") :]
    assert main.count('class="section depth-1 chapter"') == 1
    assert main.count('class="section depth-2"') == 2
    chapter_open = main.index(f'id="{chapter}"')
    chapter_close = main.rindex("</section>")
    assert chapter_open < main.index(f'id="{second}"') < main.index(f'id="{third}"')
    assert main.index(f'id="{third}"') < chapter_close
    assert main.count("</section>\n</section>") == 1  # children close before the chapter
    # The TOC nests the same way.
    assert (
        f'<ol><li><a href="#{chapter}">Chapter One</a><ol><li><a href="#{second}">Phần Hai</a>'
        in html
    )


def test_pdf_outline_sets_reading_order_and_chapters(workspace: WorkspacePaths) -> None:
    # The manifest is flat (every MinerU title is level 1) and in the wrong order.
    chapter, second, third = _section_ids(workspace)
    sections = {s.id: s for s in _sections(workspace)}
    flat = [
        sections[i].model_copy(update={"level": 1})
        for i in (third, chapter, second)
    ]
    write_sections(flat, workspace.sources_sections / DOC)
    _write_outline(
        workspace,
        [("Part One", 0, 1), ("Chapter One", 0, 2), ("Section Two", 1, 3), ("Section Three", 2, 3)],
    )

    export = build_translated_export(workspace, DOC, lang="vi", generated_at=GENERATED_AT)

    assert [(e.section_id, e.depth, e.parent_id) for e in export.report.sections] == [
        (chapter, 2, None),
        (second, 3, chapter),
        (third, 3, chapter),
    ]
    toc = export.html[export.html.index('<nav class="toc">') : export.html.index("</nav>")]
    assert toc.index("Chapter One") < toc.index("Section Two") < toc.index("Section Three")
    assert "<h2>Chapter One</h2>" in export.html
    assert "<h3>Section Two</h3>" in export.html


@pytest.mark.parametrize("fallback", ["original", "skip"])
def test_default_export_has_no_status_text(
    workspace: WorkspacePaths, fallback: FallbackPolicy
) -> None:
    chapter, second, third = _section_ids(workspace)
    _register(workspace, chapter, "# Chương Một\n\nCũ.\n")
    _translate(workspace, second, "# Phần Hai\n\nSửa tay.\n")  # untracked
    _change_section_text(workspace, chapter)  # stale

    export = build_translated_export(
        workspace, DOC, lang="vi", fallback=fallback, generated_at=GENERATED_AT
    )

    assert [e.freshness for e in export.report.sections] == ["stale", "untracked", None]
    assert {TRANSLATION_STALE, TRANSLATION_UNTRACKED} <= {w.code for w in export.report.warnings}
    # The rendered book (not the stylesheet), with section anchors — ids, not text — removed.
    body = export.html[export.html.index("<body>") :]
    body = body.replace(f'id="{DOC}.', 'id="').replace(f'href="#{DOC}.', 'href="#')
    for leak in (
        "(original)",
        "(skipped)",
        "(not tracked)",
        "(may be outdated)",
        "may be outdated",
        "Translation status unknown",
        "Untranslated",
        "Not translated yet",
        "Missing asset",
        "coverage",
        "fallback",
        "data-source",
        "source-",
        DOC,
    ):
        assert leak not in body, leak
