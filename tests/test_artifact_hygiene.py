from __future__ import annotations

import pytest

from bookgraph.annotations import annotation_hygiene_findings, build_annotation
from bookgraph.artifact_hygiene import (
    ArtifactHygieneError,
    ensure_clean_artifact,
    scan_artifact_text,
    strip_operational_lines,
)
from bookgraph.models import AnnotatedConcept, SectionAnnotation

# Leaks observed in real DDIA translation artifacts and generated reading PDFs.
LEAKS = [
    ("MEDIA:/Users/me/workspace/exports/ddia.vi-progress.pdf", "media_marker"),
    ("  MEDIA: /tmp/ddia/ch1.png", "media_marker"),
    ("✅ Đã lưu cache/enrich và mark read: ddia.ch1-intro", "progress_footer"),
    ("_Đã lưu cache và mark read: ddia.ch1_", "progress_footer"),
    ("Saved translation; called mark_read for ddia.ch1.", "progress_footer"),
    ("Marked read: ddia.ch1-intro", "progress_footer"),
    ("QA: ✅ thuật ngữ khớp glossary.", "qa_note"),
    ("✅ QA: passed", "qa_note"),
    ("QA/checker notes: none", "qa_note"),
    ("**QA note:** the checker found no untranslated sentences.", "qa_note"),
    ("> Checker: 0 issues", "qa_note"),
    ("Internal validation notes: headings match the source.", "qa_note"),
    ("- Ghi chú QA: giữ nguyên thuật ngữ 'replication'.", "qa_note"),
    ("[QA] figure 1-1 kept as-is", "qa_note"),
    ("Translation status unknown — it may be outdated", "export_status"),
    ("Untranslated — original text", "export_status"),
    ("Missing asset: images/fig1-1.png", "export_status"),
    ("(original)", "export_status"),
    ("(tracked)", "export_status"),
    ("warning: asset_missing: ddia.ch1: image 'x.png' was not found", "export_warning"),
    ("Renderer warning: font 'Noto Serif' not found", "export_warning"),
    ("![Hình 1-1](/Users/me/bookgraph/sources/parsed/ddia/images/fig1.png)", "absolute_asset_link"),
    ("![Hình 1-1](file:///tmp/fig1.png)", "absolute_asset_link"),
    ('<img src="/Users/me/fig1.png" alt="x">', "absolute_asset_link"),
    ("[fig]: /Users/me/fig.png", "absolute_asset_link"),
]

# Book content that resembles the rules but is content, not diagnostics.
CLEAN_TEXT = """\
# Chương 1. Ứng dụng đáng tin cậy, có thể mở rộng và dễ bảo trì

Nhiều ứng dụng ngày nay *data-intensive* chứ không phải *compute-intensive*.
Qatar: một ví dụ về triển khai đa vùng (multi-region).
Validation of user input happens before the write is acknowledged.
Messages are marked read once the client acknowledges them.
The original (2017) edition used the term "master"; this one uses "leader".

![Hình 1-1. Một kiến trúc dữ liệu](images/fig1-1.png)

<img src="images/fig1-2.png" alt="Hình 1-2">

```
WARNING: replication lag exceeds 30s
MEDIA:/var/log/app.log
QA: this is a code listing
```

Xem [chương 5](../ddia.ch5.md) và [tài liệu](https://example.com/docs).

Media: print, radio, and television shaped the twentieth century.
Self-check: What is a write-ahead log?
**Self-check:** Why do replicas diverge?
Internal validation: the model was checked against a held-out set.
QA: quality assurance, the team that tests a release.
Checker: a program that verifies proofs.

## The Iliad (original)

### Bread that is old (stale)

See [the docs](/docs/intro), <a href="/about">about</a>, and
![logo](//cdn.example.com/logo.png).

[docs]: /docs/intro

Call `mark_read` after the batch; a stale entry reports `translation_stale`.

    QA: an indented code listing
    mark_read(plan_id)
"""


@pytest.mark.parametrize(("line", "code"), LEAKS)
def test_each_leak_is_found_with_its_code(line: str, code: str) -> None:
    findings = scan_artifact_text(f"# Tiêu đề\n\nNội dung.\n{line}\n")

    assert [(f.code, f.line) for f in findings] == [(code, 4)]


def test_book_content_is_not_flagged() -> None:
    assert scan_artifact_text(CLEAN_TEXT) == []


def test_fence_must_close_with_the_same_marker() -> None:
    text = "~~~\n```\nQA: passed\n~~~\nQA: passed\n"

    assert [f.line for f in scan_artifact_text(text)] == [5]


def test_strip_drops_operational_lines_and_keeps_links() -> None:
    text = (
        "# Chương 1\n\nĐoạn văn.\n\n![Hình](/abs/fig.png)\n\n"
        "QA: ok\nĐã lưu cache/enrich và mark read: ddia.ch1\nMEDIA:/tmp/x.pdf\n"
    )

    stripped, dropped = strip_operational_lines(text)

    assert stripped == "# Chương 1\n\nĐoạn văn.\n\n![Hình](/abs/fig.png)\n\n"
    assert [f.code for f in dropped] == ["qa_note", "progress_footer", "media_marker"]
    assert strip_operational_lines(CLEAN_TEXT) == (CLEAN_TEXT, [])


def test_ensure_clean_lists_findings_in_the_error() -> None:
    with pytest.raises(ArtifactHygieneError) as excinfo:
        ensure_clean_artifact("Nội dung.\nQA: ok\nQA: passed\nQA: ✅\nMEDIA:/x\n", "translation")

    assert len(excinfo.value.findings) == 4
    message = str(excinfo.value)
    assert message.startswith("translation contains operational text")
    assert "line 2 (qa_note): QA: ok" in message
    assert "and 1 more" in message


def test_annotation_build_refuses_diagnostics_before_collapsing_lines() -> None:
    # ``build_annotation`` collapses the summary to one line; the line-anchored MEDIA
    # rule must still see the raw input.
    with pytest.raises(ValueError, match="annotation summary"):
        build_annotation("ddia", "ddia.ch1", None, summary="Tóm tắt.\nMEDIA:/tmp/x.png")
    with pytest.raises(ValueError, match="gloss of concept 'Replication'"):
        build_annotation(
            "ddia",
            "ddia.ch1",
            [AnnotatedConcept(slug="", title="Replication", gloss="QA: verified")],
        )

    clean = build_annotation("ddia", "ddia.ch1", None, summary="Chương giới thiệu độ tin cậy.")
    assert annotation_hygiene_findings(clean) == []


def test_stored_annotation_findings_cover_summary_and_glosses() -> None:
    annotation = SectionAnnotation(
        doc_id="ddia",
        section_id="ddia.ch1",
        summary="Tóm tắt. Đã lưu cache/enrich và mark read: ddia.ch1",
        concepts=[AnnotatedConcept(slug="x", title="X", gloss="see translation_stale")],
    )

    assert [f.code for f in annotation_hygiene_findings(annotation)] == [
        "progress_footer",
        "export_warning",
    ]


def test_collapsed_mode_finds_line_start_leaks_mid_text() -> None:
    summary = "Explains the write-ahead log. QA: checked against source MEDIA:/Users/me/out.pdf"

    assert scan_artifact_text(summary) == []  # the line-start rules see one line
    assert [f.code for f in scan_artifact_text(summary, collapsed=True)] == ["media_marker"]
    assert [f.code for f in scan_artifact_text("Tóm tắt. QA: ✅ ok", collapsed=True)] == ["qa_note"]
    assert scan_artifact_text("The original edition (original)", collapsed=True) == []


def test_stored_collapsed_summary_is_contaminated() -> None:
    annotation = SectionAnnotation(
        doc_id="ddia",
        section_id="ddia.ch1",
        summary="Explains the write-ahead log. QA: checked against source",
    )

    assert [f.code for f in annotation_hygiene_findings(annotation)] == ["qa_note"]


def test_indented_line_inside_a_paragraph_is_prose() -> None:
    # An indented line cannot start a code block mid-paragraph.
    assert [f.code for f in scan_artifact_text("Đoạn văn\n    MEDIA:/tmp/x.pdf\n")] == [
        "media_marker"
    ]
