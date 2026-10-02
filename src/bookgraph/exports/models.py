"""Data contracts for reader-facing exports (``bookgraph export ...``).

The export report is written next to the exported file and printed by the CLI, so
its warning codes are part of the artifact contract (``docs/cli/artifacts.md``):
treat them as stable strings, not display text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FallbackPolicy = Literal["original", "skip", "fail"]
FALLBACK_POLICIES: tuple[str, ...] = ("original", "skip", "fail")

# How one section ended up in the export: its translation artifact, the original
# source text (``--fallback original``), or a placeholder (``--fallback skip``).
SectionSource = Literal["translated", "original", "skipped"]

# Registry status of a rendered translation (``bookgraph.translations``): ``fresh`` (made
# from the section's current content), ``stale`` (the section changed since), or
# ``untracked`` (no valid registry record — freshness unknown).
TranslationFreshness = Literal["fresh", "stale", "untracked"]

# Export warning codes. Quality warnings from :mod:`bookgraph.quality` (e.g.
# ``asset_captions_only``) are passed through with their own codes.
ASSET_MISSING = "asset_missing"
ASSET_REMOTE = "asset_remote"
ASSET_UNSUPPORTED = "asset_unsupported"
TRANSLATION_EMPTY = "translation_empty"
TRANSLATION_MISSING_ASSETS = "translation_missing_assets"
TRANSLATION_STALE = "translation_stale"
TRANSLATION_STRUCTURE_CHANGED = "translation_structure_changed"
TRANSLATION_UNREADABLE = "translation_unreadable"
TRANSLATION_UNTRACKED = "translation_untracked"

# Warning codes that mean "an asset the reader should see is not in the export".
ASSET_WARNING_CODES: frozenset[str] = frozenset({ASSET_MISSING, ASSET_REMOTE, ASSET_UNSUPPORTED})

# ``--strict`` refuses to write an export carrying any of these: a missing asset, or a
# translation known to be outdated, to have left out the section's figures/tables, or to
# have changed a link destination / anchor / path of its source section.
# ``translation_untracked`` only warns — its freshness is unknown, not known-bad.
STRICT_WARNING_CODES: frozenset[str] = ASSET_WARNING_CODES | {
    TRANSLATION_MISSING_ASSETS,
    TRANSLATION_STALE,
    TRANSLATION_STRUCTURE_CHANGED,
}


class ExportWarning(BaseModel):
    """One problem found while assembling an export.

    ``reference`` is the raw asset reference (a Markdown image link or a parsed
    block's asset path) for an asset-scoped warning, ``None`` otherwise.
    ``source_path`` is the workspace-relative file that carries that reference — the
    translation artifact, or the parsed ``document.json`` for an original section —
    and ``block_id`` the parsed block when the reference came from one.
    """

    code: str
    message: str
    section_id: str | None = None
    reference: str | None = None
    source_path: str | None = None
    block_id: str | None = None


class ExportSection(BaseModel):
    """Where one section's content came from in the export.

    ``freshness`` is the translation's registry status when ``source`` is
    ``translated``, ``None`` otherwise.
    """

    section_id: str
    title: str
    level: int
    source: SectionSource
    artifact: str | None = None
    freshness: TranslationFreshness | None = None
    assets_embedded: int = 0
    assets_missing: int = 0


class ExportReport(BaseModel):
    """Coverage + QA report for one translated export, in reading order.

    ``coverage`` is ``translated_sections / total_sections`` (``0.0`` for an empty
    document). ``output``/``renderer`` stay ``None`` for a preflight-only run.
    ``show_status`` records whether status/debug metadata was also printed on the
    reading pages (``--show-status``); by default it lives only in this report.
    """

    doc_id: str
    title: str
    lang: str
    fallback: FallbackPolicy
    generated_at: str
    total_sections: int
    translated_sections: int
    coverage: float
    sections: list[ExportSection] = Field(default_factory=list)
    warnings: list[ExportWarning] = Field(default_factory=list)
    renderer: str | None = None
    output: str | None = None
    show_status: bool = False

    @property
    def untranslated(self) -> list[str]:
        return [entry.section_id for entry in self.sections if entry.source != "translated"]

    @property
    def asset_warnings(self) -> list[ExportWarning]:
        return [warning for warning in self.warnings if warning.code in ASSET_WARNING_CODES]

    @property
    def strict_warnings(self) -> list[ExportWarning]:
        return [warning for warning in self.warnings if warning.code in STRICT_WARNING_CODES]
