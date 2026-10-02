"""The bilingual (``original | mixed``) layout of ``bookgraph export translated-pdf``.

In ``bilingual`` mode every section becomes one two-column row: the original section
on the left and, on the right, exactly what ``translated`` mode renders for it (the
translation, or the fallback). A block-aligned translation
(:mod:`bookgraph.translation_alignment`) is laid out paragraph by paragraph instead:
one row per aligned unit, its source blocks on the left and its translation on the
right (units that split one block share a row, and source blocks no unit translates —
figures, headings — get rows of their own). Unaligned translations, and alignments
that no longer fit the section, keep the section-level row.

The assembler in :mod:`bookgraph.exports.translated` renders both columns, marking
where each unit and each source block starts with an HTML comment
(:func:`mark_units`, :func:`block_marker`); this module splits the columns at those
marks, lays the rows out, styles the page, and drops the marks.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from html import escape

from markdown_it.token import Token

from bookgraph.exports.html_attrs import HTML_ATTR_RE, HTML_START_TAG_RE
from bookgraph.exports.models import ExportReport, ExportSection
from bookgraph.models import AlignedUnit
from bookgraph.translation_alignment import AlignmentCheck, unit_blocks

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


# Where a unit of the mixed column and a source block of the original column start.
_UNIT_MARK_RE = re.compile(r"<!--bg:unit=(\d+)-->\n?")
_BLOCK_MARK_RE = re.compile(r"<!--bg:block=(.*?)-->")


def block_marker(block_id: str) -> str:
    """The mark placed before a source block's HTML in the original rendering."""

    return f"<!--bg:block={block_id}-->"


def mark_units(tokens: list[Token], body: str, units: Sequence[AlignedUnit]) -> list[Token]:
    """``tokens`` (parsed from ``body``) with a mark before each aligned unit's first block.

    A unit whose Markdown continues the previous unit's block (``unit_not_a_block``)
    gets no mark; :func:`bilingual_section` folds it into the previous unit's row.
    """

    if not units:
        return tokens
    marked: list[Token] = []
    current = -1
    for token, unit in zip(tokens, unit_blocks(tokens, body, units), strict=True):
        if unit > current:
            mark = Token("html_block", "", 0)
            mark.content = f"<!--bg:unit={unit}-->\n"
            marked.append(mark)
            current = unit
        marked.append(token)
    return marked


def strip_marks(html: str) -> str:
    """``html`` without the unit and block marks."""

    return _BLOCK_MARK_RE.sub("", _UNIT_MARK_RE.sub("", html))


def bilingual_section(
    entry: ExportSection,
    mixed: str,
    original: tuple[str, int, int],
    lang: str,
    *,
    alignment: AlignmentCheck | None = None,
    block_ids: Sequence[str] = (),
) -> tuple[ExportSection, str]:
    """Pair a section's mixed rendering with its original column.

    ``original`` is the left column's ``(html, assets_embedded, assets_missing)``.
    ``alignment`` is the translation's alignment check (``None`` for a fallback row)
    and ``block_ids`` the section's source blocks in reading order. Returns the entry
    with its original-column asset counts, alignment status and row count, and the
    rows' HTML.
    """

    html, embedded, missing = original
    pairs = (
        _aligned_pairs(html, mixed, alignment.units, block_ids)
        # An original rebuilt from ``Section.text`` (no parsed blocks) has no block marks
        # to set beside the units: keep the section row.
        if alignment is not None and alignment.status == "aligned" and _BLOCK_MARK_RE.search(html)
        else [(strip_marks(html), strip_marks(mixed))]
    )
    entry = entry.model_copy(
        update={
            "original_assets_embedded": embedded,
            "original_assets_missing": missing,
            "alignment": alignment.status if alignment is not None else None,
            "bilingual_rows": len(pairs),
        }
    )
    # The mixed column carries the same anchors (a translation keeps them; a fallback
    # row repeats the original), so only it keeps ids and in-page links land there.
    return entry, "".join(_row(strip_anchor_ids(left), right, lang) for left, right in pairs)


def _split_marks(html: str, pattern: re.Pattern[str]) -> tuple[str, dict[str, str]]:
    """The HTML before the first mark, and the HTML after each mark by its key."""

    parts = pattern.split(html)
    pieces: dict[str, str] = {}
    for key, piece in zip(parts[1::2], parts[2::2], strict=True):
        pieces[key] = pieces.get(key, "") + piece
    return parts[0], pieces


def _aligned_pairs(
    original: str, mixed: str, units: Sequence[AlignedUnit], block_ids: Sequence[str]
) -> list[tuple[str, str]]:
    """The ``(original, mixed)`` cells of an aligned section, one row per unit.

    The first row holds both headings. Units that share a block (a split) share a row,
    and so does a unit that rendered inside the previous one (no mark of its own); a
    row's original cell runs from its first source block to its last, and source blocks
    no unit references fill rows of their own, with an empty mixed cell.
    """

    original_head, blocks = _split_marks(_UNIT_MARK_RE.sub("", original), _BLOCK_MARK_RE)
    mixed_head, translated = _split_marks(_BLOCK_MARK_RE.sub("", mixed), _UNIT_MARK_RE)
    position = {block_id: index for index, block_id in enumerate(block_ids)}
    groups: list[tuple[list[str], list[int]]] = []
    for index, unit in enumerate(units):
        if groups and (
            str(index) not in translated or set(unit.source_block_ids) & set(groups[-1][0])
        ):
            groups[-1][0].extend(unit.source_block_ids)
            groups[-1][1].append(index)
        else:
            groups.append((list(unit.source_block_ids), [index]))
    pairs = [(original_head, mixed_head)]
    cursor = 0
    for ids, indexes in groups:
        first = min(position[block_id] for block_id in ids)
        last = max(position[block_id] for block_id in ids)
        pairs.extend((blocks.get(block_id, ""), "") for block_id in block_ids[cursor:first])
        left = "".join(blocks.get(block_id, "") for block_id in block_ids[first : last + 1])
        pairs.append((left, "".join(translated.get(str(i), "") for i in indexes)))
        cursor = last + 1
    pairs.extend((blocks.get(block_id, ""), "") for block_id in block_ids[cursor:])
    rows: list[tuple[str, str]] = []
    for left, right in pairs:
        if not left.strip() and not right.strip():
            continue
        if rows and not right.strip() and not rows[-1][1].strip():
            rows[-1] = (rows[-1][0] + left, "")  # consecutive untranslated blocks
        else:
            rows.append((left, right))
    return rows


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

    return HTML_START_TAG_RE.sub(replace, html)


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
