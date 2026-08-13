from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from bookgraph.models import Document, Section


class DocumentParser(ABC):
    """Parse an input document into canonical blocks."""

    name: str

    @abstractmethod
    def parse(self, source: Path, output_dir: Path) -> Document:
        raise NotImplementedError


class DocumentSegmenter(ABC):
    """Split canonical blocks into human reading units."""

    name: str

    @abstractmethod
    def segment(self, document: Document) -> list[Section]:
        raise NotImplementedError


class WikiBackend(ABC):
    """Compile/source sections into a graph wiki backend."""

    name: str

    @abstractmethod
    def compile_book(
        self,
        sections: list[Section],
        output_dir: Path,
        concepts_dir: Path | None = None,
    ) -> Path:
        raise NotImplementedError
