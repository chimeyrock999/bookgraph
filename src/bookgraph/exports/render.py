"""HTML for the translated reading edition: page shell, title page, TOC, sections.

:mod:`bookgraph.exports.translated` decides *what* goes in the book (each section's
heading and body, its place in :mod:`bookgraph.exports.outline`); this module decides
how the assembled book is laid out as one self-contained HTML page. Status markup
(coverage title page, per-section notes, TOC markers) is only emitted with
``show_status``; by default the page carries the book's content only.
"""

from __future__ import annotations

from html import escape

from bookgraph.exports.bilingual import columns_legend, page_style
from bookgraph.exports.models import ExportReport, ExportSection
from bookgraph.exports.outline import OutlineNode


def heading(level: int, title: str) -> str:
    tag = f"h{max(1, min(level, 6))}"
    return f"<{tag}>{escape(title)}</{tag}>"


_STYLE = """
@page { size: A4; margin: 22mm 20mm 24mm 20mm;
  @bottom-center { content: counter(page); font-size: 9pt; color: #666; } }
html { font-family: "Noto Serif", "Source Serif 4", "DejaVu Serif", "Times New Roman", serif;
  font-size: 11pt; line-height: 1.5; color: #111; }
body { margin: 0; }
h1, h2, h3, h4, h5, h6 { font-family: "Noto Sans", "Source Sans 3", "DejaVu Sans",
  "Helvetica Neue", Arial, sans-serif; line-height: 1.25; break-after: avoid; }
.frontmatter { break-after: page; }
.frontmatter dl { display: grid; grid-template-columns: max-content 1fr; gap: 2pt 12pt; }
.frontmatter dt { font-weight: bold; }
.frontmatter dd { margin: 0; }
.notice { color: #555; font-size: 9.5pt; }
.title-page { break-after: page; padding-top: 30%; text-align: center; }
.toc { break-after: page; }
.toc ol { list-style: none; padding-left: 0; }
.toc ol ol { padding-left: 12pt; }
.toc li { margin: 1pt 0; }
.toc a { color: inherit; text-decoration: none; }
.toc .status { color: #888; font-size: 9pt; }
.section.chapter { break-before: page; }
.source-note, .placeholder { color: #8a5a00; font-size: 9pt; font-style: italic; }
.source-skipped .placeholder { border: 1px dashed #c9a24a; padding: 6pt; }
figure { margin: 10pt 0; text-align: center; break-inside: avoid; }
figure img, p img { max-width: 100%; max-height: 220mm; }
figcaption { font-size: 9pt; color: #444; margin-top: 3pt; }
.missing-asset { display: inline-block; border: 1px dashed #b00; color: #b00;
  padding: 4pt 6pt; font-size: 9pt; }
.equation { font-family: "DejaVu Sans Mono", Menlo, monospace; font-size: 9.5pt;
  margin: 6pt 0; white-space: pre-wrap; }
table { border-collapse: collapse; margin: 8pt 0; font-size: 9.5pt; }
th, td { border: 1px solid #999; padding: 2pt 5pt; vertical-align: top; }
pre, code { font-family: "DejaVu Sans Mono", Menlo, monospace; font-size: 9pt; }
pre { white-space: pre-wrap; background: #f5f5f5; padding: 6pt; }
"""

# Self-contained page: images are data: URIs and nothing else may load or run, so a
# script or remote reference inside a translation artifact stays inert in any viewer.
_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"

# Notes under a fallback section's heading, rendered only with ``show_status``.
UNTRANSLATED_NOTE = '<p class="source-note">Untranslated — original text</p>'
SKIPPED_NOTE = '<p class="placeholder">Not translated yet — omitted from this export.</p>'

# Status markers below are rendered only with ``show_status``; a reader-facing export
# keeps them in the report.
_STATUS_LABELS = {"original": "original", "skipped": "skipped"}

# Shown under the heading of a translated section whose freshness is not ``fresh``,
# like the "Untranslated — original text" label of a fallback section.
FRESHNESS_NOTES = {
    "stale": '<p class="source-note">Translation may be outdated — the original section '
    "changed after it was translated</p>",
    "untracked": '<p class="source-note">Translation status unknown — it may be outdated</p>',
}

# TOC markers for the same states, so the overview shows every non-fresh translation.
_FRESHNESS_LABELS = {"stale": "may be outdated", "untracked": "not tracked"}


def document_html(
    report: ExportReport,
    outline: list[OutlineNode],
    bodies: dict[str, str],
    *,
    show_status: bool,
) -> str:
    entries = {entry.section_id: entry for entry in report.sections}
    toc = (
        '<nav class="toc"><h2>Contents</h2>'
        + _toc_list(outline, entries, show_status=show_status)
        + "</nav>\n"
    )
    main = "".join(
        _section_tree(node, entries, bodies, show_status=show_status) for node in outline
    )
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="{escape(report.lang)}">\n<head>\n<meta charset="utf-8">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">\n'
        f'<meta name="generator" content="bookgraph export translated-pdf">\n'
        f"<title>{escape(report.title)}</title>\n"
        f"<style>{_STYLE}{page_style(report)}</style>\n</head>\n<body>\n"
        + front_matter(report, show_status=show_status)
        + toc
        + "<main>\n"
        + main
        + "</main>\n</body>\n</html>\n"
    )


def front_matter(report: ExportReport, *, show_status: bool) -> str:
    """The title page: the title (and bilingual legend), or the status frontmatter."""

    return _status_frontmatter(report) if show_status else _title_page(report)


def _title_page(report: ExportReport) -> str:
    return (
        f'<header class="title-page"><h1>{escape(report.title)}</h1>'
        f"{columns_legend(report)}</header>\n"
    )


def _status_frontmatter(report: ExportReport) -> str:
    percent = f"{report.coverage * 100:.1f}%"
    return (
        '<header class="frontmatter">'
        f"<h1>{escape(report.title)}</h1>"
        "<dl>"
        f"<dt>doc_id</dt><dd>{escape(report.doc_id)}</dd>"
        f"<dt>language</dt><dd>{escape(report.lang)}</dd>"
        f"<dt>mode</dt><dd>{escape(report.mode)}</dd>"
        f"<dt>generated</dt><dd>{escape(report.generated_at)}</dd>"
        f"<dt>coverage</dt><dd>{report.translated_sections}/{report.total_sections} "
        f"sections translated ({percent})</dd>"
        f"<dt>fallback</dt><dd>{escape(report.fallback)}</dd>"
        "</dl>"
        + columns_legend(report)
        + '<p class="notice">Generated by BookGraph from section-level artifacts. A reading '
        "edition in progress — not a reproduction of the original page layout.</p>"
        "</header>\n"
    )


def _toc_list(
    nodes: list[OutlineNode], entries: dict[str, ExportSection], *, show_status: bool
) -> str:
    items = []
    for node in nodes:
        entry = entries[node.section.id]
        marker = ""
        if show_status:
            status = _STATUS_LABELS.get(entry.source)
            if entry.freshness is not None:
                status = _FRESHNESS_LABELS.get(entry.freshness, status)
            marker = f' <span class="status">({status})</span>' if status else ""
        children = (
            _toc_list(node.children, entries, show_status=show_status) if node.children else ""
        )
        items.append(
            f'<li><a href="#{escape(entry.section_id)}">{escape(entry.title)}</a>'
            f"{marker}{children}</li>"
        )
    return "<ol>" + "".join(items) + "</ol>"


def _section_tree(
    node: OutlineNode,
    entries: dict[str, ExportSection],
    bodies: dict[str, str],
    *,
    show_status: bool,
) -> str:
    """A section's ``<section>`` with its child sections nested inside it."""

    entry = entries[node.section.id]
    classes = f"section depth-{node.depth}" + (" chapter" if node.chapter else "")
    data = ""
    if show_status:
        classes += f" source-{entry.source}"
        data = f' data-source="{entry.source}"'
    children = "".join(
        _section_tree(child, entries, bodies, show_status=show_status) for child in node.children
    )
    return (
        f'<section class="{classes}" id="{escape(entry.section_id)}"{data}>'
        f"{bodies[entry.section_id]}{children}</section>\n"
    )
