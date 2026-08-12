from __future__ import annotations

from pathlib import Path

from bookgraph.parsers.errors import UnsupportedSourceError
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

__all__ = [
    "MARKDOWN_PARSER_NAME",
    "MARKDOWN_SUFFIXES",
    "MARKITDOWN_PARSER_NAME",
    "MARKITDOWN_SUFFIXES",
    "MINERU_MIDDLE_JSON_PARSER_NAME",
    "ParserRouter",
    "UnsupportedSourceError",
    "select_parser_name",
]


def select_parser_name(source: Path) -> str:
    """Pick a parser plugin name from the source file type.

    Routing only chooses an adapter; it never runs parser, segmenter, or wiki
    work. Deterministic structure first: MinerU output wins for PDF-derived
    sources, Markdown is parsed directly, and everything else goes through
    MarkItDown.

    Two source types are refused on purpose rather than guessed:

    - a raw ``.pdf``, because the MinerU adapter consumes MinerU's
      ``*_middle.json`` output and never invokes MinerU itself;
    - a plain ``.json`` that is not MinerU output, because routing it to the
      MinerU adapter would produce a silently empty document.
    """

    suffix = source.suffix.lower()

    if source.name.lower().endswith(MINERU_MIDDLE_JSON_SUFFIX):
        return MINERU_MIDDLE_JSON_PARSER_NAME
    if suffix in MARKDOWN_SUFFIXES:
        return MARKDOWN_PARSER_NAME
    if suffix in MARKITDOWN_SUFFIXES:
        return MARKITDOWN_PARSER_NAME
    if suffix == ".json":
        raise UnsupportedSourceError(
            f"{source.name}: JSON input must be a MinerU *{MINERU_MIDDLE_JSON_SUFFIX} file. "
            f"Pass --parser {MINERU_MIDDLE_JSON_PARSER_NAME} to force it."
        )
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
