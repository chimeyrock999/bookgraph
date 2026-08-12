from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from bookgraph.utils import slugify
from bookgraph.workspace import WorkspacePaths


@dataclass(frozen=True)
class BookRegistration:
    book_id: str
    title: str
    source_type: str
    source_path: Path
    workspace: WorkspacePaths

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
        return self.workspace.wiki_root / "books" / self.book_id

    def manifest(self) -> dict[str, object]:
        return {
            "book_id": self.book_id,
            "title": self.title,
            "source_type": self.source_type,
            "source_path": str(self.source_path),
            "workspace_path": str(self.workspace.root),
            "status": "registered",
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
    return BookRegistration(
        book_id=slugify(title),
        title=title,
        source_type="pdf",
        source_path=resolved_source,
        workspace=workspace,
    )


def register_book(registration: BookRegistration) -> None:
    registration.book_root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(registration.source_path, registration.original_path)
    registration.manifest_path.write_text(json.dumps(registration.manifest(), indent=2) + "\n")


def _title_from_path(source: Path) -> str:
    return source.stem.replace("_", " ").strip().title()
