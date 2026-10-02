"""DOCX fixes layered onto MarkItDown's mammoth-based converter.

MarkItDown turns a DOCX into HTML with mammoth, then into Markdown with markdownify. Two losses
happen on that path, and both are fixed here before markdownify sees the HTML:

- mammoth has no rule for Word's ``Title`` style, so the document title comes out as a plain
  paragraph and the first ``Heading 1`` becomes the document title instead;
- markdownify pads a ``colspan`` but ignores ``rowspan``, so every row under a vertical merge
  loses a cell and its columns shift left, and it renders a table nested in a cell as raw pipe
  text that the Markdown reader then splits apart (the inner table vanishes from the blocks).

Rowspans are padded with empty cells so columns stay aligned. A nested table cannot be
expressed in a Markdown table cell, so it is flattened into the cell as text (cells joined by
``" / "``, rows by ``"; "``) and counted, letting the parser warn that the structure changed.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag
from markitdown import MarkItDown
from markitdown.converters import DocxConverter, HtmlConverter

# Prepended to any caller/embedded map; mammoth uses the first matching rule.
TITLE_STYLE_MAP = "p[style-name='Title'] => h1:fresh"

NESTED_CELL_SEPARATOR = " / "
NESTED_ROW_SEPARATOR = "; "


class _TableNormalizingHtmlConverter(HtmlConverter):  # type: ignore[misc, unused-ignore]
    """HTML converter that rewrites tables into a shape markdownify renders faithfully."""

    def __init__(self) -> None:
        super().__init__()
        self.flattened_nested_tables = 0

    def convert_string(self, html_content: str, *, url: str | None = None, **kwargs: Any) -> Any:
        html_content, flattened = normalize_tables(html_content)
        self.flattened_nested_tables += flattened
        return super().convert_string(html_content, url=url, **kwargs)


class BookgraphDocxConverter(DocxConverter):  # type: ignore[misc, unused-ignore]
    """MarkItDown's DOCX converter plus the ``Title`` style and table normalization."""

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call, unused-ignore]
        self._tables = _TableNormalizingHtmlConverter()
        # DocxConverter hands mammoth's HTML to this attribute; replacing it is the narrowest hook
        # that keeps MarkItDown's own DOCX pre-processing (math, comments, style maps).
        self._html_converter = self._tables

    @property
    def flattened_nested_tables(self) -> int:
        return self._tables.flattened_nested_tables

    def reset(self) -> None:
        self._tables.flattened_nested_tables = 0

    def convert(self, file_stream: Any, stream_info: Any, **kwargs: Any) -> Any:
        kwargs["style_map"] = "\n".join(
            part for part in (TITLE_STYLE_MAP, kwargs.get("style_map")) if part
        )
        return super().convert(file_stream, stream_info, **kwargs)


class BookgraphConversion:
    def __init__(self, text_content: str, flattened_nested_tables: int) -> None:
        self.text_content = text_content
        self.flattened_nested_tables = flattened_nested_tables


class BookgraphMarkItDown:
    """MarkItDown configured for source-grounded parsing.

    - ``keep_data_uris`` keeps embedded images (DOCX/PPTX) whole instead of truncating them to
      ``data:image/png;base64...``, so the parser can decode and stage them;
    - DOCX goes through :class:`BookgraphDocxConverter`.
    """

    def __init__(self) -> None:
        self._docx = BookgraphDocxConverter()
        self._markitdown = MarkItDown()
        # Later registrations win at equal priority, so this replaces the built-in DOCX converter.
        self._markitdown.register_converter(self._docx)

    def convert(self, source: str) -> BookgraphConversion:
        self._docx.reset()
        result = self._markitdown.convert(source, keep_data_uris=True)
        return BookgraphConversion(result.text_content, self._docx.flattened_nested_tables)


def normalize_tables(html: str) -> tuple[str, int]:
    """Pad rowspans and flatten nested tables; return the HTML and the flattened-table count."""

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return html, 0
    flattened = 0
    # Innermost first: a nested table is flattened into its cell before its parent is padded.
    for table in reversed(tables):
        if table.find_parent("table") is not None:
            table.replace_with(_flatten_table(table))
            flattened += 1
        else:
            _pad_rowspans(soup, table)
    return str(soup), flattened


def _rows(table: Tag) -> list[Tag]:
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def _cells(row: Tag) -> list[Tag]:
    return [cell for cell in row.find_all(["td", "th"], recursive=False)]


def _span(cell: Tag, attribute: str) -> int:
    value = str(cell.get(attribute) or "1")
    return max(1, min(1000, int(value))) if value.isdigit() else 1


def _pad_rowspans(soup: BeautifulSoup, table: Tag) -> None:
    """Insert an empty cell under every rowspan so each row keeps the full column count."""

    # Column where a rowspan starts -> (rows it still covers, its colspan).
    pending: dict[int, tuple[int, int]] = {}
    for row in _rows(table):
        col = 0
        for cell in _cells(row):
            col = _fill(soup, row, cell, col, pending)
            width = _span(cell, "colspan")
            height = _span(cell, "rowspan")
            if height > 1:
                pending[col] = (height - 1, width)
                del cell["rowspan"]
            col += width
        # Rowspans to the right of the row's last cell still owe it a cell each.
        for start in sorted(pending):
            if start >= col:
                col = _fill(soup, row, None, start, pending)


def _fill(
    soup: BeautifulSoup,
    row: Tag,
    before: Tag | None,
    col: int,
    pending: dict[int, tuple[int, int]],
) -> int:
    """Place the empty cells that rowspans owe this row at ``col``; return the next column."""

    while col in pending:
        remaining, width = pending.pop(col)
        filler = soup.new_tag("td")
        if width > 1:
            filler["colspan"] = str(width)
        if before is None:
            row.append(filler)
        else:
            before.insert_before(filler)
        if remaining > 1:
            pending[col] = (remaining - 1, width)
        col += width
    return col


def _flatten_table(table: Tag) -> str:
    rows = []
    for row in _rows(table):
        texts = [" ".join(cell.get_text(" ").split()) for cell in _cells(row)]
        if any(texts):
            rows.append(NESTED_CELL_SEPARATOR.join(texts))
    return NESTED_ROW_SEPARATOR.join(rows)
