"""Data contracts for reader-facing exports (``bookgraph export ...``).

The export report is written next to the exported file and printed by the CLI, so
its warning codes are part of the artifact contract (``docs/cli/artifacts.md``):
treat them as stable strings, not display text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from bookgraph.models import AlignmentStatus

FallbackPolicy = Literal["original", "skip", "fail"]
FALLBACK_POLICIES: tuple[str, ...] = ("original", "skip", "fail")

# The reader-facing layout: ``translated`` is the mixed reading edition (each section's
# translation, else the ``--fallback`` result); ``bilingual`` puts the original section
# on the left and that same mixed rendering on the right.
ExportMode = Literal["translated", "bilingual"]
EXPORT_MODES: tuple[str, ...] = ("translated", "bilingual")

# How ``bilingual`` mode pairs a row's original with its reading edition, decided by the
# output format: ``columns`` sets them side by side in a two-column table (paged PDF and
# HTML); ``interleaved`` puts the original first and the translation after it, which is
# what reflowable screens (EPUB) need.
BilingualLayout = Literal["columns", "interleaved"]

# How one section ended up in the export: its translation artifact, the original
# source text (``--fallback original``), or a placeholder (``--fallback skip``).
SectionSource = Literal["translated", "original", "skipped"]

# Registry status of a rendered translation (``bookgraph.translations``): ``fresh`` (made
# from the section's current content), ``stale`` (the section changed since), or
# ``untracked`` (no valid registry record — freshness unknown).
TranslationFreshness = Literal["fresh", "stale", "untracked"]

# Which rendering a warning belongs to: ``mixed`` is the reading edition (the whole page
# in ``translated`` mode, the right column in ``bilingual`` mode); ``original`` is the
# bilingual left column only, so ``--mode translated`` never reports it.
WarningColumn = Literal["mixed", "original"]

# What content a warning is about: the translation artifact, or the original source
# (parsed document / sections manifest) rendered as a fallback or as the left column.
WarningOrigin = Literal["translation", "source"]

# Export warning codes. Quality warnings from :mod:`bookgraph.quality` (e.g.
# ``asset_captions_only``) are passed through with their own codes.
ASSET_MISSING = "asset_missing"
ASSET_REMOTE = "asset_remote"
ASSET_UNSUPPORTED = "asset_unsupported"
INTERNAL_LINK_UNRESOLVED = "internal_link_unresolved"
TRANSLATION_EMPTY = "translation_empty"
TRANSLATION_MISSING_ASSETS = "translation_missing_assets"
TRANSLATION_STALE = "translation_stale"
TRANSLATION_STRUCTURE_CHANGED = "translation_structure_changed"
TRANSLATION_UNREADABLE = "translation_unreadable"
TRANSLATION_UNTRACKED = "translation_untracked"
XHTML_REPAIRED = "xhtml_repaired"

# Warning codes that mean "an asset the reader should see is not in the export".
ASSET_WARNING_CODES: frozenset[str] = frozenset({ASSET_MISSING, ASSET_REMOTE, ASSET_UNSUPPORTED})

# ``--strict`` refuses to write an export carrying any of these: a missing asset, or a
# translation known to be outdated, to have left out the section's figures/tables, or to
# have changed a link destination / anchor / path of its source section.
# ``translation_untracked`` only warns — its freshness is unknown, not known-bad, and so
# does ``internal_link_unresolved`` until source-anchor mapping covers more books.
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
    ``column`` says which rendering the problem is in (an ``original`` one exists only
    in ``bilingual`` mode) and ``origin`` whether it comes from the translation or the
    original source.
    """

    code: str
    message: str
    section_id: str | None = None
    reference: str | None = None
    source_path: str | None = None
    block_id: str | None = None
    column: WarningColumn = "mixed"
    origin: WarningOrigin = "source"

    def describe(self) -> str:
        """The message, tagged when only the bilingual original column shows the problem."""

        if self.column == "original":
            return f"[original column] {self.message}"
        return self.message


class ExportSection(BaseModel):
    """Where one section's content came from in the export, and where it sits.

    ``freshness`` is the translation's registry status when ``source`` is
    ``translated``, ``None`` otherwise. ``level`` is the manifest's ``Section.level``;
    ``depth`` (heading level in the export) and ``parent_id`` (the section it renders
    inside, ``None`` for a top-level one) are its place in the book's structure, which
    follows the source PDF outline when there is one. ``assets_*`` count the mixed
    rendering (the whole section in ``translated`` mode, the right column in
    ``bilingual`` mode); ``original_assets_*`` count the bilingual left column and stay
    ``None`` in ``translated`` mode. In ``bilingual`` mode ``alignment`` is a translated
    section's block alignment (``aligned`` rows interleave unit by unit; ``unaligned``
    and ``invalid`` ones pair the whole section) and ``bilingual_rows`` the rows it
    took; both stay ``None`` in ``translated`` mode, and ``alignment`` for a fallback.
    """

    section_id: str
    title: str
    level: int
    depth: int = 1
    parent_id: str | None = None
    source: SectionSource
    artifact: str | None = None
    freshness: TranslationFreshness | None = None
    assets_embedded: int = 0
    assets_missing: int = 0
    original_assets_embedded: int | None = None
    original_assets_missing: int | None = None
    alignment: AlignmentStatus | None = None
    bilingual_rows: int | None = None


class ExportReport(BaseModel):
    """Coverage + QA report for one translated export, in the export's reading order.

    ``coverage`` is ``translated_sections / total_sections`` (``0.0`` for an empty
    document). ``original_sections`` / ``skipped_sections`` count the sections that
    took the ``--fallback`` path. ``unpaired_sections`` counts bilingual rows with no
    translation to compare against (``0`` in ``translated`` mode), and
    ``assets_missing`` the asset references that could not be embedded, per column.
    ``output``/``renderer`` stay ``None`` for a preflight-only run.
    ``show_status`` records whether status/debug metadata was also printed on the
    reading pages (``--show-status``); by default it lives only in this report.
    ``source_lang`` is the original text's language (``--source-lang``), ``None`` when
    unknown: original-language content is then tagged ``und``.
    """

    doc_id: str
    title: str
    lang: str
    source_lang: str | None = None
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
    show_status: bool = False

    @classmethod
    def from_sections(
        cls,
        sections: list[ExportSection],
        *,
        mode: ExportMode,
        doc_id: str,
        title: str,
        lang: str,
        fallback: FallbackPolicy,
        generated_at: str,
        warnings: list[ExportWarning],
        show_status: bool = False,
        source_lang: str | None = None,
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
            doc_id=doc_id,
            title=title,
            lang=lang,
            source_lang=source_lang,
            fallback=fallback,
            generated_at=generated_at,
            warnings=warnings,
            show_status=show_status,
        )

    @property
    def original_lang(self) -> str:
        """The ``lang`` value of original-language content: ``und`` when unknown."""

        return self.source_lang or "und"

    @property
    def untranslated(self) -> list[str]:
        return [entry.section_id for entry in self.sections if entry.source != "translated"]

    @property
    def asset_warnings(self) -> list[ExportWarning]:
        return [warning for warning in self.warnings if warning.code in ASSET_WARNING_CODES]

    @property
    def strict_warnings(self) -> list[ExportWarning]:
        return [warning for warning in self.warnings if warning.code in STRICT_WARNING_CODES]

    def strict_summary(self) -> str:
        """Why ``--strict`` refuses this export: the count, split by column when needed.

        Problems of the bilingual original column are counted apart: they are source
        assets, not translation problems, and ``--mode translated`` does not render them.
        """

        problems = self.strict_warnings
        summary = f"{len(problems)} problem(s) in strict mode"
        original = sum(1 for warning in problems if warning.column == "original")
        if original:
            summary += (
                f" ({original} in the bilingual original column: source assets that "
                "--mode translated does not render)"
            )
        return summary
