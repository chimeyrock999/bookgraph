from __future__ import annotations

from pathlib import Path

from bookgraph.plugins import PluginRegistry
from bookgraph.ports import DocumentParser
from bookgraph.utils import MINERU_MIDDLE_JSON_SUFFIX

MINERU_MIDDLE_JSON_PARSER_NAME = "mineru-middle-json"
MARKDOWN_PARSER_NAME = "markdown"
MARKITDOWN_PARSER_NAME = "markitdown"

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdx"})
MARKITDOWN_SUFFIXES = frozenset(
    {
        ".csv",
        ".doc",
        ".docx",
        ".epub",
        ".htm",
        ".html",
        ".ppt",
        ".pptx",
        ".txt",
        ".xls",
        ".xlsx",
    }
)


class UnsupportedSourceError(ValueError):
    """Raised when no bundled parser plugin can handle a source file."""


def select_parser_name(source: Path) -> str:
    """Pick a parser plugin name from the source file type.

    Routing only chooses an adapter; it never runs parser, segmenter, or wiki
    work. Deterministic structure first: MinerU output wins for PDF-derived
    sources, Markdown is parsed directly, and everything else goes through
    MarkItDown.

    A raw PDF is refused on purpose. The MinerU adapter consumes MinerU's
    ``*_middle.json`` output, not the PDF itself, so routing a ``.pdf`` to it
    would only fail later inside the parser.
    """

    suffix = source.suffix.lower()

    if source.name.lower().endswith(MINERU_MIDDLE_JSON_SUFFIX) or suffix == ".json":
        return MINERU_MIDDLE_JSON_PARSER_NAME
    if suffix in MARKDOWN_SUFFIXES:
        return MARKDOWN_PARSER_NAME
    if suffix in MARKITDOWN_SUFFIXES:
        return MARKITDOWN_PARSER_NAME
    if suffix == ".pdf":
        raise UnsupportedSourceError(
            f"{source.name}: PDFs need an explicit parser. Run MinerU and parse its "
            f"*{MINERU_MIDDLE_JSON_SUFFIX} output, or pass "
            f"--parser {MARKITDOWN_PARSER_NAME} for simple text-only PDFs."
        )

    raise UnsupportedSourceError(
        f"{source.name}: unsupported source type '{suffix or source.name}'. "
        "Pass --parser to choose a plugin explicitly."
    )


class ParserRouter:
    """Object wrapper over :func:`select_parser_name` for injectable routing."""

    def parser_name_for(self, source: Path) -> str:
        return select_parser_name(source)

    def parser_for(
        self, source: Path, parsers: PluginRegistry[DocumentParser]
    ) -> DocumentParser:
        return parsers.get(self.parser_name_for(source))
