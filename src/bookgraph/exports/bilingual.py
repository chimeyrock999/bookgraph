"""The bilingual (``original | mixed``) layout of ``bookgraph export translated-pdf``.

In ``bilingual`` mode every section becomes one two-column row: the original section
on the left and, on the right, exactly what ``translated`` mode renders for it (the
translation, or the fallback). Rows align at section level; translations are free
Markdown with no block ids, so finer alignment is not attempted. The assembler in
:mod:`bookgraph.exports.translated` renders both columns; this module only lays the
row out and styles the page.
"""

from __future__ import annotations

import re
from html import escape

from bookgraph.exports.html_attrs import HTML_ATTR, HTML_ATTR_RE
from bookgraph.exports.models import ExportReport, ExportSection

# Any start tag, with HTML comments matched first so a commented-out tag is left alone.
_HTML_START_TAG_RE = re.compile(
    rf"(?P<comment><!--.*?-->)|<(?P<name>[A-Za-z][\w:-]*)(?P<attrs>(?:\s+{HTML_ATTR})*)\s*(?P<end>/?>)",
    re.DOTALL,
)

# Added to the page style in bilingual mode: two columns need a landscape page and a
# smaller type size.
BILINGUAL_STYLE = """
@page { size: A4 landscape; margin: 16mm 14mm 18mm 14mm; }
html { font-size: 10pt; }
.columns-legend { color: #555; font-size: 9.5pt; }
table.bilingual { width: 100%; table-layout: fixed; border-collapse: collapse; margin: 0;
  font-size: inherit; }
table.bilingual td.column { width: 50%; border: none; padding: 0 8pt; vertical-align: top;
  overflow-wrap: break-word; }
table.bilingual td.column-original { border-right: 1px solid #ccc; padding-left: 0; }
table.bilingual td.column-mixed { padding-right: 0; }
td.column pre, td.column code, td.column a { overflow-wrap: anywhere; }
/* A nested table is held to its column: words wrap at boundaries and only a word
   wider than its cell breaks, instead of the table running into the other column. */
td.column table { width: 100%; table-layout: fixed; font-size: 8pt; }
td.column img { max-width: 100%; height: auto; max-height: 160mm; }
td.column-original :is(h1, h2, h3, h4, h5, h6) { bookmark-level: none; }
"""

# How the legend describes untranslated rows. ``fail`` never writes an export.
_LEGEND_FALLBACK = {
    "original": "Untranslated sections repeat the original text.",
    "skip": "Untranslated sections are left out.",
    "fail": "",
}


def bilingual_section(
    entry: ExportSection, mixed: str, original: tuple[str, int, int], lang: str
) -> tuple[ExportSection, str]:
    """Pair a section's mixed rendering with its original column.

    ``original`` is the left column's ``(html, assets_embedded, assets_missing)``.
    Returns the entry with its original-column asset counts and the row's inner HTML.
    """

    html, embedded, missing = original
    entry = entry.model_copy(
        update={"original_assets_embedded": embedded, "original_assets_missing": missing}
    )
    # The mixed column carries the same anchors (a translation keeps them; a fallback
    # row repeats the original), so only it keeps ids and in-page links land there.
    return entry, _row(strip_anchor_ids(html), mixed, lang)


def page_style(report: ExportReport) -> str:
    """Extra page CSS for the report's mode (empty in ``translated`` mode)."""

    return BILINGUAL_STYLE if report.mode == "bilingual" else ""


def columns_legend(report: ExportReport) -> str:
    """The frontmatter line that says which column is which (``bilingual`` only).

    How untranslated rows read is status metadata, so it is added only with
    ``show_status``.
    """

    if report.mode != "bilingual":
        return ""
    fallback = f" {_LEGEND_FALLBACK[report.fallback]}" if report.show_status else ""
    return (
        '<p class="columns-legend">Left column: original text. Right column: '
        f"{escape(report.lang)} reading edition.{fallback}</p>"
    )


def strip_anchor_ids(html: str) -> str:
    """Drop ``id`` attributes, and ``name`` on ``<a>``, from every start tag in ``html``.

    Link targets (``href``) are untouched; only the anchors they point at go.
    """

    def replace(match: re.Match[str]) -> str:
        if match.group("comment"):
            return match.group(0)
        tag = match.group("name")
        dropped = {"id", "name"} if tag.lower() == "a" else {"id"}
        attrs = "".join(
            f" {attr.group(0)}"
            for attr in HTML_ATTR_RE.finditer(match.group("attrs"))
            if attr.group(1).lower() not in dropped
        )
        return f"<{tag}{attrs}{match.group('end')}"

    return _HTML_START_TAG_RE.sub(replace, html)


def _row(original: str, mixed: str, lang: str) -> str:
    """One section as a two-column row: original left, mixed reading edition right.

    A table, not grid/flex: both PDF backends paginate table rows reliably, and the
    fixed layout keeps the columns at equal width whatever their content.
    """

    return (
        '<table class="bilingual"><tr>'
        # No source language is stored, so the original is "undetermined" rather than
        # inheriting the page's target language.
        f'<td class="column column-original" data-column="original" lang="und">{original}</td>'
        f'<td class="column column-mixed" data-column="mixed" lang="{escape(lang, quote=True)}">'
        f"{mixed}</td></tr></table>"
    )
