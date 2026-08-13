from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

try:  # pypdf is an optional parser dependency.
    from pypdf import PdfReader as _PdfReader
except ImportError:  # pragma: no cover - exercised only without the parsers extra
    _PdfReader = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class PdfBookmark:
    title: str
    page_index: int | None
    level: int


@dataclass(frozen=True)
class PdfMetadata:
    title: str | None
    author: str | None
    pages: int
    bookmarks: list[PdfBookmark]

    @property
    def has_bookmarks(self) -> bool:
        return bool(self.bookmarks)


def inspect_pdf_metadata(pdf: Path) -> PdfMetadata:
    """Read cheap PDF metadata and bookmarks for future TOC-aware segmentation."""

    if _PdfReader is None:
        raise RuntimeError(
            "pypdf is required for PDF metadata inspection. Install with: uv sync --extra parsers"
        )
    reader_cls = cast(Any, _PdfReader)
    reader = reader_cls(str(pdf))
    metadata = reader.metadata
    return PdfMetadata(
        title=_clean_text(getattr(metadata, "title", None)) if metadata else None,
        author=_clean_text(getattr(metadata, "author", None)) if metadata else None,
        pages=len(reader.pages),
        bookmarks=_flatten_outline(reader),
    )


def _flatten_outline(reader: Any) -> list[PdfBookmark]:
    bookmarks: list[PdfBookmark] = []

    def walk(items: list[Any], level: int) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, level + 1)
                continue
            title = _clean_text(getattr(item, "title", None))
            if not title:
                continue
            try:
                page_index = reader.get_destination_page_number(item)
            except Exception:  # pragma: no cover - malformed outlines vary by producer
                page_index = None
            bookmarks.append(PdfBookmark(title=title, page_index=page_index, level=level))

    walk(list(getattr(reader, "outline", []) or []), 1)
    return bookmarks


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
