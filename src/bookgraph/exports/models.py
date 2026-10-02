"""Data contracts for reader-facing exports (``bookgraph export ...``).

The export report is written next to the exported file and printed by the CLI, so
its warning codes are part of the artifact contract (``docs/cli/artifacts.md``):
treat them as stable strings, not display text.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

FallbackPolicy = Literal["original", "skip", "fail"]
FALLBACK_POLICIES: tuple[str, ...] = ("original", "skip", "fail")

# The reader-facing layout: ``translated`` is the mixed reading edition (each section's
# translation, else the ``--fallback`` result); ``bilingual`` puts the original section
# on the left and that same mixed rendering on the right.
ExportMode = Literal["translated", "bilingual"]
EXPORT_MODES: tuple[str, ...] = ("translated", "bilingual")

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
TRANSLATION_UNREADABLE = "translation_unreadable"
TRANSLATION_UNTRACKED = "translation_untracked"

# Warning codes that mean "an asset the reader should see is not in the export".
ASSET_WARNING_CODES: frozenset[str] = frozenset({ASSET_MISSING, ASSET_REMOTE, ASSET_UNSUPPORTED})

# ``--strict`` refuses to write an export carrying any of these: a missing asset, or a
# translation known to be outdated or to have left out the section's figures/tables.
# ``translation_untracked`` only warns — its freshness is unknown, not known-bad.
STRICT_WARNING_CODES: frozenset[str] = ASSET_WARNING_CODES | {
    TRANSLATION_MISSING_ASSETS,
    TRANSLATION_STALE,
}


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
    """Where one section's content came from in the export.

    ``freshness`` is the translation's registry status when ``source`` is
    ``translated``, ``None`` otherwise. ``assets_*`` count the mixed rendering (the
    whole section in ``translated`` mode, the right column in ``bilingual`` mode);
    ``original_assets_*`` count the bilingual left column and stay ``None`` in
    ``translated`` mode.
    """

    section_id: str
    title: str
    level: int
    source: SectionSource
    artifact: str | None = None
    freshness: TranslationFreshness | None = None
    assets_embedded: int = 0
    assets_missing: int = 0
    original_assets_embedded: int | None = None
    original_assets_missing: int | None = None


class ExportReport(BaseModel):
    """Coverage + QA report for one translated export, in reading order.

    ``coverage`` is ``translated_sections / total_sections`` (``0.0`` for an empty
    document). ``original_sections`` / ``skipped_sections`` count the sections that
    took the ``--fallback`` path. ``unpaired_sections`` counts bilingual rows with no
    translation to compare against (``0`` in ``translated`` mode), and
    ``assets_missing`` the asset references that could not be embedded, per column.
    ``output``/``renderer`` stay ``None`` for a preflight-only run.
    """

    doc_id: str
    title: str
    lang: str
    mode: ExportMode = "translated"
    fallback: FallbackPolicy
    generated_at: str
    total_sections: int
    translated_sections: int
    original_sections: int = 0
    skipped_sections: int = 0
    unpaired_sections: int = 0
    assets_missing: int = 0
    coverage: float
    sections: list[ExportSection] = Field(default_factory=list)
    warnings: list[ExportWarning] = Field(default_factory=list)
    renderer: str | None = None
    output: str | None = None

    @classmethod
    def from_sections(
        cls, sections: list[ExportSection], *, mode: ExportMode, **fields: Any
    ) -> ExportReport:
        """A report for ``sections`` with its coverage and fallback counts filled in."""

        translated = sum(1 for entry in sections if entry.source == "translated")
        total = len(sections)
        return cls(
            mode=mode,
            sections=sections,
            total_sections=total,
            translated_sections=translated,
            original_sections=sum(1 for entry in sections if entry.source == "original"),
            skipped_sections=sum(1 for entry in sections if entry.source == "skipped"),
            unpaired_sections=total - translated if mode == "bilingual" else 0,
            assets_missing=sum(
                entry.assets_missing + (entry.original_assets_missing or 0) for entry in sections
            ),
            coverage=round(translated / total, 4) if total else 0.0,
            **fields,
        )

    @property
    def untranslated(self) -> list[str]:
        return [entry.section_id for entry in self.sections if entry.source != "translated"]

    @property
    def asset_warnings(self) -> list[ExportWarning]:
        return [warning for warning in self.warnings if warning.code in ASSET_WARNING_CODES]

    @property
    def strict_warnings(self) -> list[ExportWarning]:
        return [warning for warning in self.warnings if warning.code in STRICT_WARNING_CODES]

