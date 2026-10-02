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

# Export warning codes. Quality warnings from :mod:`bookgraph.quality` (e.g.
# ``asset_captions_only``) are passed through with their own codes.
ASSET_MISSING = "asset_missing"
ASSET_REMOTE = "asset_remote"
ASSET_UNSUPPORTED = "asset_unsupported"
TRANSLATION_EMPTY = "translation_empty"
TRANSLATION_UNREADABLE = "translation_unreadable"

# Warning codes that mean "an asset the reader should see is not in the export".
# ``--strict`` refuses to write an export carrying any of them.
ASSET_WARNING_CODES: frozenset[str] = frozenset({ASSET_MISSING, ASSET_REMOTE, ASSET_UNSUPPORTED})


class ExportWarning(BaseModel):
    """One problem found while assembling an export.

    ``reference`` is the raw asset reference (a Markdown image link or a parsed
    block's asset path) for an asset-scoped warning, ``None`` otherwise.
    """

    code: str
    message: str
    section_id: str | None = None
    reference: str | None = None


class ExportSection(BaseModel):
    """Where one section's content came from in the export."""

    section_id: str
    title: str
    level: int
    source: SectionSource
    artifact: str | None = None
    assets_embedded: int = 0
    assets_missing: int = 0


class ExportReport(BaseModel):
    """Coverage + QA report for one translated export, in reading order.

    ``coverage`` is ``translated_sections / total_sections`` (``0.0`` for an empty
    document). ``output``/``renderer`` stay ``None`` for a preflight-only run.
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

    @property
    def untranslated(self) -> list[str]:
        return [entry.section_id for entry in self.sections if entry.source != "translated"]

    @property
    def asset_warnings(self) -> list[ExportWarning]:
        return [warning for warning in self.warnings if warning.code in ASSET_WARNING_CODES]
