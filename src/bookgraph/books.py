from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from bookgraph.pdf_metadata import PdfMetadata, inspect_pdf_metadata
from bookgraph.utils import slugify
from bookgraph.workspace import WorkspacePaths


@dataclass(frozen=True)
class BookRegistration:
    book_id: str
    title: str
    source_type: str
    source_path: Path
    workspace: WorkspacePaths
    pdf_metadata: PdfMetadata | None = None

    @property
    def book_root(self) -> Path:
        return self.workspace.sources_inbox / self.book_id

    @property
    def manifest_path(self) -> Path:
        return self.book_root / "book.json"

    @property
    def original_path(self) -> Path:
        return self.book_root / f"original.{self.source_type}"

    @property
    def parsed_path(self) -> Path:
        return self.workspace.sources_parsed / self.book_id

    @property
    def sections_path(self) -> Path:
        return self.workspace.sources_sections / self.book_id

    @property
    def wiki_path(self) -> Path:
        return self.workspace.wiki_books / self.book_id

    def manifest(self) -> dict[str, object]:
        return {
            "book_id": self.book_id,
            "title": self.title,
            "source_type": self.source_type,
            "source_path": str(self.source_path),
            "workspace_path": str(self.workspace.root),
            "status": "registered",
            "pdf": _pdf_manifest(self.pdf_metadata),
            "pipeline": {
                "parser": None,
                "segmenter": None,
                "wiki_backend": None,
            },
            "paths": {
                "book_root": str(self.book_root),
                "original": str(self.original_path),
                "parsed": str(self.parsed_path),
                "sections": str(self.sections_path),
                "wiki": str(self.wiki_path),
            },
        }


def build_book_registration(workspace: WorkspacePaths, source: Path) -> BookRegistration:
    resolved_source = source.expanduser().resolve()
    if resolved_source.suffix.lower() != ".pdf":
        raise ValueError("Only PDF input is supported by this CLI contract for now.")
    title = _title_from_path(resolved_source)
    pdf_metadata = _try_inspect_pdf_metadata(resolved_source)
    return BookRegistration(
        book_id=slugify(title),
        title=title,
        source_type="pdf",
        source_path=resolved_source,
        workspace=workspace,
        pdf_metadata=pdf_metadata,
    )


def register_book(registration: BookRegistration) -> None:
    registration.book_root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(registration.source_path, registration.original_path)
    registration.manifest_path.write_text(json.dumps(registration.manifest(), indent=2) + "\n")


def _title_from_path(source: Path) -> str:
    return source.stem.replace("_", " ").strip().title()


def _try_inspect_pdf_metadata(source: Path) -> PdfMetadata | None:
    try:
        return inspect_pdf_metadata(source)
    except Exception:
        # Registration should remain cheap and tolerant: malformed PDFs or missing
        # optional pypdf support should not block copying the original source.
        return None


def _pdf_manifest(metadata: PdfMetadata | None) -> dict[str, object]:
    if metadata is None:
        return {
            "title": None,
            "author": None,
            "pages": 0,
            "has_bookmarks": False,
            "bookmarks": [],
        }
    return {
        "title": metadata.title,
        "author": metadata.author,
        "pages": metadata.pages,
        "has_bookmarks": metadata.has_bookmarks,
        "bookmarks": [
            {
                "title": bookmark.title,
                "page_index": bookmark.page_index,
                "level": bookmark.level,
            }
            for bookmark in metadata.bookmarks
        ],
    }
