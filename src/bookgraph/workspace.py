from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspacePaths:
    """Canonical output locations inside a BookGraph workspace."""

    root: Path

    @property
    def config(self) -> Path:
        return self.root / "bookgraph.toml"

    @property
    def sources_inbox(self) -> Path:
        return self.root / "sources" / "inbox"

    @property
    def sources_parsed(self) -> Path:
        return self.root / "sources" / "parsed"

    @property
    def sources_sections(self) -> Path:
        return self.root / "sources" / "sections"

    @property
    def llmwiki_root(self) -> Path:
        """Project root the standalone llmwiki compiler serves.

        The ``llm-wiki-compiler`` contract is ``llmwiki serve --root <project>``
        over a project whose top-level ``sources/*.md`` are ingested and whose
        ``wiki/`` + ``.llmwiki/`` are generated output. That project root is the
        BookGraph workspace root itself; the bridge stages sections into
        :attr:`llmwiki_sources` (``sources/``) and llmwiki owns its own lifecycle
        under this root without touching BookGraph's canonical inputs.
        """
        return self.root

    @property
    def llmwiki_sources(self) -> Path:
        """Top-level ``sources/*.md`` directory the llmwiki compiler ingests.

        The bridge writes one staged source file per section here. BookGraph's own
        canonical inputs live in *sub*directories (``sources/inbox``,
        ``sources/parsed``, ``sources/sections``); staged llmwiki sources are
        top-level ``sources/*.md`` files and never collide with them.
        """
        return self.root / "sources"

    @property
    def wiki_root(self) -> Path:
        return self.root / "wiki"

    @property
    def wiki_concepts(self) -> Path:
        return self.wiki_root / "concepts"

    @property
    def wiki_books(self) -> Path:
        return self.wiki_root / "books"

    @property
    def wiki_comparisons(self) -> Path:
        return self.wiki_root / "comparisons"

    @property
    def wiki_daily(self) -> Path:
        return self.wiki_root / "daily"

    @property
    def indexes_root(self) -> Path:
        return self.root / "indexes"

    @property
    def annotations_root(self) -> Path:
        return self.root / "annotations"

    @property
    def reading_plans_root(self) -> Path:
        return self.root / "reading_plans"

    @property
    def runs_root(self) -> Path:
        return self.root / "runs"

    def directories(self) -> list[Path]:
        return [
            self.sources_inbox,
            self.sources_parsed,
            self.sources_sections,
            self.wiki_concepts,
            self.wiki_books,
            self.wiki_comparisons,
            self.wiki_daily,
            self.indexes_root,
            self.annotations_root,
            self.reading_plans_root,
            self.runs_root,
        ]

    def as_mapping(self) -> dict[str, Path]:
        return {
            "root": self.root,
            "config": self.config,
            "sources.inbox": self.sources_inbox,
            "sources.parsed": self.sources_parsed,
            "sources.sections": self.sources_sections,
            "wiki.root": self.wiki_root,
            "wiki.concepts": self.wiki_concepts,
            "wiki.books": self.wiki_books,
            "wiki.comparisons": self.wiki_comparisons,
            "wiki.daily": self.wiki_daily,
            "indexes.root": self.indexes_root,
            "annotations.root": self.annotations_root,
            "reading_plans.root": self.reading_plans_root,
            "runs.root": self.runs_root,
        }


def default_config(paths: WorkspacePaths) -> str:
    return f'''# BookGraph workspace config
output_root = "{paths.root}"

[parsers]
default_pdf = "mineru-middle-json"
default_office = "markitdown"
default_markdown = "markdown"

[segmenter]
default = "heading"
target_level = 2
max_tokens = 800

[wiki]
backend = "llmwiki"

[mcp]
server = "bookgraph"
'''
