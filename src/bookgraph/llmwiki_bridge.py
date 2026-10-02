"""Bridge BookGraph section artifacts into a standalone llmwiki project.

BookGraph's canonical reading graph lives in ``sources/sections/<doc_id>/`` and
``indexes/bookgraph.db``. The standalone ``llm-wiki-compiler`` tool has its own
project lifecycle: it ingests ``sources/**/*.md`` files, runs
``llmwiki compile`` (incrementally, tracked in ``.llmwiki/state.json``), and
writes compiled pages under ``wiki/``.

This module is the explicit, deterministic bridge between the two. It stages one
or more BookGraph sections as individual llmwiki source files so that:

- a large book is never routed through one truncating full-book ingest — each
  section is its own bounded source file;
- BookGraph provenance (``doc_id`` / ``section_id``) is preserved in each staged
  file's frontmatter, so compiled pages can trace back to the reading graph;
- a new project groups sources by book (``sources/<doc_id>/<section_id>.md``,
  with ``sources.recursive`` enabled in ``.llmwiki/config.json``), so the
  llmwiki viewer's Sources screen lists each book as one folder;
- re-running the bridge is idempotent — an unchanged section is left untouched on
  disk (stable mtime), so llmwiki's own incremental compile skips it and a daily
  batch is added without reprocessing the whole book.

It never reads or mutates BookGraph's canonical inputs; it only *writes* derived
llmwiki source files. BookGraph MCP stays decoupled from llmwiki entirely.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from bookgraph.models import Section

PROJECT_CONFIG = {"version": 1, "sources": {"recursive": True}}
"""``.llmwiki/config.json`` the bridge writes when it creates the llmwiki project."""


@dataclass(frozen=True)
class BridgeResult:
    """Outcome of staging sections into an llmwiki ``sources/`` directory."""

    sources_dir: Path
    staged: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)


def staged_source_name(section: Section) -> str:
    """Filename for a staged llmwiki source.

    Section ids are already filename-safe slugs and unique within a document
    (enforced by :func:`bookgraph.sections.write_sections`), so ``<id>.md`` is a
    stable, collision-free name that also mirrors the canonical section Markdown
    file name.
    """

    return f"{section.id}.md"


def render_llmwiki_source(section: Section) -> str:
    """Render one section as a self-contained llmwiki source document.

    The frontmatter carries a ``title`` for llmwiki plus ``bookgraph_*``
    provenance keys so a compiled page can be traced back to its ``doc_id`` /
    ``section_id``. Values are emitted as JSON scalars/arrays (valid YAML), so a
    title containing colons or quotes cannot corrupt the frontmatter.
    """

    fields: dict[str, object] = {
        "title": section.title,
        "bookgraph_doc_id": section.doc_id,
        "bookgraph_section_id": section.id,
        "bookgraph_heading_path": section.heading_path,
    }
    lines = ["---"]
    lines += [f"{key}: {json.dumps(value)}" for key, value in fields.items()]
    lines.append("---")

    heading = "#" * max(1, min(section.level, 6))
    body = f"{heading} {section.title}"
    if section.text:
        body += f"\n\n{section.text}"
    return "\n".join(lines) + "\n\n" + body + "\n"


def ensure_project_config(llmwiki_root: Path) -> bool:
    """Decide the staging layout, writing the project config for a new project.

    Returns ``True`` when sources are staged nested per document
    (``sources/<doc_id>/``), which needs llmwiki's opt-in ``sources.recursive``.

    - New project (no ``sources/`` and no ``.llmwiki/config.json``): write
      :data:`PROJECT_CONFIG` and stage nested.
    - Existing config: never rewritten; nested only if it already sets
      ``sources.recursive: true``.
    - Existing flat ``sources/`` without a config: stay flat. Nesting it now
      would leave the old flat copies in place and compile every section twice.
    """

    config_path = llmwiki_root / ".llmwiki" / "config.json"
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text())
        except (OSError, ValueError):
            return False
        sources = config.get("sources") if isinstance(config, dict) else None
        return isinstance(sources, dict) and sources.get("recursive") is True
    if (llmwiki_root / "sources").exists():
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(PROJECT_CONFIG, indent=2) + "\n")
    return True


def stage_sections(
    sections: list[Section], sources_dir: Path, *, nested: bool = False
) -> BridgeResult:
    """Stage ``sections`` as individual llmwiki source files, idempotently.

    With ``nested``, each file goes under ``sources_dir/<doc_id>/``; otherwise
    directly under ``sources_dir``. Files are always regular files: llmwiki
    (>= 1.3) does not compile symlinked sources.

    A section whose staged file already exists with identical content is left
    untouched (reported as ``unchanged``) so its mtime stays stable and llmwiki's
    incremental compile skips it. Only new or changed sections are (re)written.
    """

    sources_dir.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    unchanged: list[Path] = []
    for section in sections:
        directory = sources_dir / section.doc_id if nested else sources_dir
        directory.mkdir(exist_ok=True)
        path = directory / staged_source_name(section)
        content = render_llmwiki_source(section)
        if path.is_file() and path.read_text() == content:
            unchanged.append(path)
            continue
        path.write_text(content)
        staged.append(path)
    return BridgeResult(sources_dir=sources_dir, staged=staged, unchanged=unchanged)
