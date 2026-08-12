from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bookgraph.models import Document
from bookgraph.parsers.markdown import document_from_markdown
from bookgraph.ports import DocumentParser
from bookgraph.utils import doc_id_from_path


class MissingParserDependencyError(RuntimeError):
    """Raised when an optional parser dependency is not installed."""


class MarkdownConversion(Protocol):
    text_content: str


class MarkdownConverter(Protocol):
    def convert(self, source: str) -> MarkdownConversion: ...


@dataclass
class MarkItDownParser(DocumentParser):
    """Adapter for Office/HTML/text sources via MarkItDown.

    MarkItDown stays an optional extra: it is imported lazily and the failure is
    reported only when this parser runs, so the base install stays lightweight.
    ``converter`` can be injected, which keeps the adapter testable without the
    dependency.
    """

    converter: MarkdownConverter | None = None
    name: str = "markitdown"

    def parse(self, source: Path, output_dir: Path) -> Document:
        converter = self.converter or _load_markitdown()
        markdown = converter.convert(str(source)).text_content
        doc_id = doc_id_from_path(source)

        output_dir.mkdir(parents=True, exist_ok=True)
        staged = output_dir / f"{doc_id}.md"
        staged.write_text(markdown)

        return document_from_markdown(
            markdown,
            doc_id=doc_id,
            fallback_title=source.stem,
            source_path=str(source),
            parser_name=self.name,
            metadata={"markdown_path": str(staged)},
        )


def _load_markitdown() -> MarkdownConverter:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise MissingParserDependencyError(
            "MarkItDown parser requires the optional parser dependencies. "
            "Install with: uv sync --extra parsers"
        ) from exc
    converter: MarkdownConverter = MarkItDown()
    return converter
