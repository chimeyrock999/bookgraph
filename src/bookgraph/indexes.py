"""Persistent inverted index backing MCP ``search``.

The segment stage writes ``sources/sections/<doc_id>/sections.jsonl``; this module
derives a per-document JSON inverted index under
``indexes/sections/<doc_id>.json`` so ``search`` can look up query terms instead
of rescanning every section's text. The index is fully regenerable from the
sections manifest, so it stores a denormalised copy of each section's title/text
purely to build result snippets without re-reading the manifest.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from bookgraph.models import Section
from bookgraph.workspace import WorkspacePaths

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase and split text into alphanumeric search tokens."""

    return _TOKEN_RE.findall(text.lower())


class IndexedSection(BaseModel):
    """A section's searchable content, denormalised into the index for snippets."""

    id: str
    title: str
    text: str


class SectionIndex(BaseModel):
    """Inverted index for one document's sections."""

    doc_id: str
    sections: list[IndexedSection] = Field(default_factory=list)
    # token -> {section_id: term frequency}
    postings: dict[str, dict[str, int]] = Field(default_factory=dict)


def index_path(workspace: WorkspacePaths, doc_id: str) -> Path:
    """Canonical location of a document's section index."""

    return workspace.indexes_root / "sections" / f"{doc_id}.json"


def build_section_index(doc_id: str, sections: list[Section]) -> SectionIndex:
    """Build an inverted index from a document's sections (in reading order)."""

    indexed: list[IndexedSection] = []
    postings: dict[str, dict[str, int]] = {}
    for section in sections:
        indexed.append(IndexedSection(id=section.id, title=section.title, text=section.text))
        for token in tokenize(f"{section.title}\n{section.text}"):
            postings.setdefault(token, {})
            postings[token][section.id] = postings[token].get(section.id, 0) + 1
    return SectionIndex(doc_id=doc_id, sections=indexed, postings=postings)


def write_index(index: SectionIndex, path: Path) -> Path:
    """Persist a section index to ``path``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(index.model_dump_json(indent=2) + "\n")
    return path


def read_index(path: Path) -> SectionIndex:
    """Load a section index written by :func:`write_index`."""

    return SectionIndex.model_validate_json(path.read_text())
