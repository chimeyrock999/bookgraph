"""Assemble a partially translated book into one reading edition (HTML, then PDF).

The original sections manifest is the skeleton: sections are emitted in
``sections.jsonl`` reading order, and each one is filled from its translation in the
translation registry (:mod:`bookgraph.translations`) for the requested language, or —
per the fallback policy — from the original parsed content, a placeholder, or not at
all. The export only reads the registry: a translation's freshness (``fresh`` /
``stale`` / ``untracked``) is reported per section in the report.

The reading pages carry book content only. Status and debug metadata — freshness
labels, "untranslated" notes, coverage, missing-asset placeholders — go to the report
JSON, and are printed on the pages only with ``show_status`` (``--show-status``), so a
reading agent never meets BookGraph's own status text in a page it reads back.

Original sections are rebuilt from their parsed ``document.json`` blocks (via
``Section.block_ids``) when available, so figures, tables and equations land next
to the prose that surrounds them in the source; a section whose blocks are not
available falls back to its ``Section.text``. Every image is embedded as a
``data:`` URI, so the assembled HTML is self-contained and a PDF renderer never
needs to touch the filesystem or network. An image that cannot be embedded (its file
is missing, remote, or not an image) is left out of the reader-facing output — its
caption and the surrounding prose stay — and reported in the export report, with the
section, the reference, and the file that carries it.

This is a clean reading edition, not a pixel-perfect reconstruction of the
publisher's layout.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

from bookgraph.assets import asset_reference, resolve_asset_path
from bookgraph.documents import read_document
from bookgraph.exports.images import AssetCounter, AssetOrigin, ImageEmbedder
from bookgraph.exports.models import (
    ASSET_MISSING,
    TRANSLATION_EMPTY,
    TRANSLATION_MISSING_ASSETS,
    TRANSLATION_STALE,
    TRANSLATION_STRUCTURE_CHANGED,
    TRANSLATION_UNREADABLE,
    TRANSLATION_UNTRACKED,
    ExportReport,
    ExportSection,
    ExportWarning,
    FallbackPolicy,
    SectionSource,
    TranslationFreshness,
)
from bookgraph.exports.renderers import ExportRenderer
from bookgraph.models import ASSET_BLOCK_TYPES, CanonicalBlock, Section
from bookgraph.quality import (
    ASSET_CAPTIONS_ONLY,
    ASSET_TEXT_SPARSE,
    asset_summaries,
    section_warnings,
)
from bookgraph.sections import read_sections
from bookgraph.translation_structure import (
    check_section_translation,
    describe_structure_issues,
    local_asset_resolver,
)
from bookgraph.translations import (
    TranslationState,
    section_content_hash,
    split_frontmatter,
    translation_state,
    validate_lang,
)
from bookgraph.utils import validate_slug_id
from bookgraph.workspace import WorkspacePaths

# Ingest quality warnings worth repeating in an export report: the section's source
# prose is mostly captions, so the reader (or translator) should inspect its assets.
_PASSTHROUGH_QUALITY_CODES = frozenset({ASSET_CAPTIONS_ONLY, ASSET_TEXT_SPARSE})


class ExportError(ValueError):
    """The export cannot be assembled from the workspace as it is."""


class UntranslatedSectionsError(ExportError):
    """``--fallback fail`` and at least one section has no translation artifact."""

    def __init__(self, report: ExportReport) -> None:
        self.report = report
        missing = report.untranslated
        super().__init__(
            f"{len(missing)} of {report.total_sections} sections have no '{report.lang}' "
            "translation: " + ", ".join(missing)
        )


@dataclass(frozen=True)
class TranslatedExport:
    """The assembled reading edition plus its coverage/QA report."""

    html: str
    report: ExportReport


def default_generated_at() -> str:
    """UTC timestamp for the export frontmatter, honouring ``SOURCE_DATE_EPOCH``.

    Reproducible-build tooling sets ``SOURCE_DATE_EPOCH`` to pin timestamps, which
    makes an export byte-for-byte repeatable over unchanged inputs.
    """

    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    seconds = int(epoch) if epoch and epoch.isdigit() else int(time.time())
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_translated_export(
    workspace: WorkspacePaths,
    doc_id: str,
    *,
    lang: str,
    fallback: FallbackPolicy = "original",
    generated_at: str | None = None,
    show_status: bool = False,
) -> TranslatedExport:
    """Assemble the reading edition for ``doc_id`` in ``lang``.

    Raises :class:`ExportError` when the sections manifest is missing or invalid,
    and :class:`UntranslatedSectionsError` when ``fallback="fail"`` and any section
    lacks a translation. Missing or unsupported assets never raise: they are
    reported in ``report.warnings`` (with the section, the file carrying the
    reference, and the reference itself) and left out of the output, keeping their
    captions — or, with ``show_status``, rendered as visible placeholders.

    ``doc_id`` is validated as a slug and ``lang`` is normalised the way the
    translation registry does it (``VI`` → ``vi``), so neither id can traverse out of
    the workspace and ``lang="VI"`` finds the ``vi`` translations.

    ``show_status`` prints status/debug metadata (freshness and fallback notes, TOC
    status markers, coverage, missing-asset placeholders) on the reading pages; by
    default it is only in the report.
    """

    try:
        validate_slug_id(doc_id, field_name="doc_id")
        lang = validate_lang(lang)
    except ValueError as exc:
        raise ExportError(str(exc)) from exc
    manifest = workspace.sources_sections / doc_id / "sections.jsonl"
    if not manifest.is_file():
        raise ExportError(
            f"Sections manifest not found: {manifest}. Run 'bookgraph segment' first."
        )
    try:
        sections = read_sections(manifest)
    except (OSError, ValueError) as exc:
        raise ExportError(f"Invalid sections manifest: {manifest}: {exc}") from exc

    parsed_dir = workspace.sources_parsed / doc_id
    title, blocks = _load_document(parsed_dir, doc_id)
    assembler = _Assembler(
        workspace=workspace,
        parsed_dir=parsed_dir,
        blocks=blocks,
        manifest=manifest,
        show_status=show_status,
    )

    rendered = [
        assembler.render_section(
            section,
            translation_state(workspace, lang, doc_id, section.id, section_content_hash(section)),
            fallback,
        )
        for section in sections
    ]
    report = _report(
        doc_id,
        title,
        lang,
        fallback,
        generated_at or default_generated_at(),
        [entry for entry, _ in rendered],
        assembler.warnings,
        show_status,
    )
    # Checked after rendering, not on artifact existence: an empty or unreadable
    # artifact falls back too, and must count as untranslated under ``fail``. Stale and
    # untracked translations are rendered, so they count as translated here; they are
    # flagged by warnings instead (and ``--strict`` refuses stale ones).
    if fallback == "fail" and report.untranslated:
        raise UntranslatedSectionsError(report)
    html = _document_html(report, [body for _, body in rendered])
    return TranslatedExport(html=html, report=report)


def _report(
    doc_id: str,
    title: str,
    lang: str,
    fallback: FallbackPolicy,
    generated_at: str,
    entries: list[ExportSection],
    warnings: list[ExportWarning],
    show_status: bool,
) -> ExportReport:
    translated = sum(1 for entry in entries if entry.source == "translated")
    total = len(entries)
    return ExportReport(
        doc_id=doc_id,
        title=title,
        lang=lang,
        fallback=fallback,
        generated_at=generated_at,
        total_sections=total,
        translated_sections=translated,
        coverage=round(translated / total, 4) if total else 0.0,
        sections=entries,
        warnings=warnings,
        show_status=show_status,
    )


def _load_document(parsed_dir: Path, doc_id: str) -> tuple[str, dict[str, CanonicalBlock]]:
    """The parsed title and blocks by id, or ``(doc_id, {})`` when not parsed.

    A sections-only workspace (no ``document.json``) still exports: originals are
    then rendered from ``Section.text`` and carry no source assets.
    """

    try:
        document = read_document(parsed_dir / "document.json")
    except (OSError, ValueError):
        return doc_id, {}
    return document.title or doc_id, {block.id: block for block in document.blocks}


def _relative(workspace: WorkspacePaths, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(workspace.root).as_posix()
    except ValueError:
        return str(path)


@dataclass(kw_only=True)
class _Assembler(ImageEmbedder):
    blocks: dict[str, CanonicalBlock]
    manifest: Path
    _md: MarkdownIt = field(
        default_factory=lambda: MarkdownIt("commonmark", {"html": True}).enable(
            ["table", "strikethrough"]
        )
    )

    # -- sections -----------------------------------------------------------------

    def render_section(
        self, section: Section, state: TranslationState, fallback: FallbackPolicy
    ) -> tuple[ExportSection, str]:
        counter = AssetCounter()
        translated = self._translated_body(section, state, counter)
        if translated is not None:
            # A body was read, so the status is fresh, stale, or untracked (never
            # missing, and never orphaned: the section exists).
            freshness: TranslationFreshness = (
                state.status if state.status in ("fresh", "stale") else "untracked"
            )
            title, heading, body = translated
            self._freshness_warnings(section, state, freshness)
            self._quality_warnings(section, source="translated")
            entry = self._entry(
                section, title, "translated", state.paths.body, counter, freshness=freshness
            )
            note = _FRESHNESS_NOTES.get(freshness, "") if self.show_status else ""
            return entry, _section_html(section, "translated", heading + note + body)
        if fallback == "skip":
            body = _heading(section.level, section.title)
            if self.show_status:
                body += '<p class="placeholder">Not translated yet — omitted from this export.</p>'
            return self._entry(section, section.title, "skipped", None, counter), (
                _section_html(section, "skipped", body)
            )
        body = _heading(section.level, section.title)
        if self.show_status:
            body += '<p class="source-note">Untranslated — original text</p>'
        body += self._original_body(section, counter)
        self._quality_warnings(section, source="original")
        return self._entry(section, section.title, "original", None, counter), (
            _section_html(section, "original", body)
        )

    def _entry(
        self,
        section: Section,
        title: str,
        source: SectionSource,
        artifact: Path | None,
        counter: AssetCounter,
        *,
        freshness: TranslationFreshness | None = None,
    ) -> ExportSection:
        return ExportSection(
            section_id=section.id,
            title=title,
            level=section.level,
            source=source,
            artifact=_relative(self.workspace, artifact),
            freshness=freshness,
            assets_embedded=counter.embedded,
            assets_missing=counter.missing,
        )

    def _translated_body(
        self, section: Section, state: TranslationState, counter: AssetCounter
    ) -> tuple[str, str, str] | None:
        """Render a registry translation as ``(title, heading_html, body_html)``.

        ``None`` means there is no usable translation and the fallback policy applies.
        The body is the exact bytes the registry status was decided on, never a re-read.
        """

        artifact = state.paths.body
        relative = _relative(self.workspace, artifact)
        if state.body is None:
            # ``translation_state`` reads an unreadable body as missing; a file that is
            # there but cannot be read is still worth reporting. Re-read only to recover
            # the cause for the message — these bytes are never rendered.
            if state.status == "missing" and artifact.exists():
                try:
                    artifact.read_bytes()
                    reason = "exists but could not be read"
                except OSError as exc:
                    reason = str(exc)
                self._warn(
                    TRANSLATION_UNREADABLE,
                    f"translation artifact {relative} is unreadable ({reason}); "
                    "using the fallback policy instead",
                    section.id,
                )
            return None
        try:
            raw = state.body.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._warn(
                TRANSLATION_UNREADABLE,
                f"translation artifact {relative} is unreadable ({exc}); "
                "using the fallback policy instead",
                section.id,
            )
            return None
        frontmatter, body = split_frontmatter(raw)
        if not body.strip():
            self._warn(
                TRANSLATION_EMPTY,
                f"translation artifact {relative} is empty; using the fallback policy instead",
                section.id,
            )
            return None
        self._structure_warnings(section, body, artifact)

        tokens = self._md.parse(body)
        heading_title = _first_heading_text(tokens)
        _shift_headings(tokens, section.level)
        self._rewrite_images(
            tokens, section.id, [artifact.parent, *self._parsed_bases()], counter, relative
        )
        fm_title = frontmatter.get("title")
        title = heading_title or (fm_title if isinstance(fm_title, str) and fm_title else None)
        if heading_title is None:
            heading = _heading(section.level, title or section.title)
        else:
            # Render the leading heading on its own so a freshness note can follow it.
            heading_end = next(i for i, t in enumerate(tokens) if t.type == "heading_close") + 1
            heading = self._md.renderer.render(tokens[:heading_end], self._md.options, {})
            tokens = tokens[heading_end:]
        html = self._md.renderer.render(tokens, self._md.options, {})
        return title or section.title, heading, html

    def _structure_warnings(self, section: Section, body: str, artifact: Path) -> None:
        """Flag a translation whose link destinations/anchors/paths differ from the source.

        Export navigation itself anchors on section ids, never on (translated) heading
        text, but intra-book links and image paths in the body only work as written.
        """

        resolves = local_asset_resolver(
            self.workspace.root, [artifact.parent, *self._parsed_bases()]
        )
        issues = check_section_translation(
            section, body, blocks=self.blocks, asset_resolves=resolves
        )
        if issues:
            self._warn(
                TRANSLATION_STRUCTURE_CHANGED,
                f"translation {_relative(self.workspace, artifact)} changed structural "
                f"Markdown of its section: {describe_structure_issues(issues)}",
                section.id,
            )

    def _freshness_warnings(
        self, section: Section, state: TranslationState, freshness: TranslationFreshness
    ) -> None:
        relative = _relative(self.workspace, state.paths.body)
        if freshness == "stale":
            self._warn(
                TRANSLATION_STALE,
                f"translation {relative} was made from an older version of the section; "
                "rendered with a 'may be outdated' note",
                section.id,
            )
        elif freshness == "untracked":
            self._warn(
                TRANSLATION_UNTRACKED,
                f"translation {relative} has no registry record (or was edited after "
                "registration), so its freshness is unknown; register it with "
                "write_section_translation",
                section.id,
            )
        # The registry's reuse rule: a translation is complete only when it carried the
        # section's figures/tables, or the section has none. Untracked bodies have no
        # ``includes_assets`` record to check.
        if (
            state.artifact is not None
            and not state.artifact.includes_assets
            and self._has_assets(section)
        ):
            self._warn(
                TRANSLATION_MISSING_ASSETS,
                f"translation {relative} is prose-only (includes_assets=false) but the "
                "section has figures/tables; they are not in this export",
                section.id,
            )

    def _has_assets(self, section: Section) -> bool:
        """Whether the section owns any figure/table asset block (staged or not)."""

        return bool(asset_summaries(self.blocks[b] for b in section.block_ids if b in self.blocks))

    def _original_body(self, section: Section, counter: AssetCounter) -> str:
        blocks = [self.blocks[b] for b in section.block_ids if b in self.blocks]
        if not blocks:
            source = _relative(self.workspace, self.manifest)
            return self._markdown(section.text, section.id, counter, source)
        source = _relative(self.workspace, self.parsed_dir / "document.json")
        parts: list[str] = []
        for index, block in enumerate(blocks):
            if block.type == "title":
                if index == 0 and block.text.strip() == section.title.strip():
                    continue  # the section heading is already rendered
                level = max(block.level or section.level + 1, section.level + 1)
                parts.append(_heading(level, block.text))
            elif block.type in ASSET_BLOCK_TYPES and asset_reference(block):
                parts.append(self._asset_block(block, section.id, counter, source))
            elif block.type == "equation":
                parts.append(f'<div class="equation">{escape(block.text)}</div>')
            elif block.text.strip():
                parts.append(self._markdown(block.text, section.id, counter, source, block.id))
        return "".join(parts)

    def _asset_block(
        self, block: CanonicalBlock, section_id: str, counter: AssetCounter, source: str | None
    ) -> str:
        caption = f"<figcaption>{escape(block.text)}</figcaption>" if block.text.strip() else ""
        reference = asset_reference(block)
        origin = AssetOrigin(source, block.id)
        resolved = resolve_asset_path(self.parsed_dir, block)
        uri = self._data_uri(Path(resolved), section_id, reference, origin) if resolved else None
        if resolved is None:
            self._warn(
                ASSET_MISSING,
                f"{block.type} asset '{reference}' of block {block.id} was not found under "
                f"{_relative(self.workspace, self.parsed_dir)}",
                section_id,
                reference,
                origin,
            )
        if uri is None:
            counter.missing += 1
            placeholder = self._missing(reference)
            if not placeholder and not caption:
                return ""
            # The caption is source prose: it stays even when the figure itself cannot.
            return f'<figure class="asset {block.type}">{placeholder}{caption}</figure>'
        counter.embedded += 1
        alt = escape(block.text, quote=True)
        return f'<figure class="asset {block.type}"><img src="{uri}" alt="{alt}">{caption}</figure>'

    def _quality_warnings(self, section: Section, *, source: SectionSource) -> None:
        if not self.blocks:
            return
        summaries = asset_summaries(
            (self.blocks[b] for b in section.block_ids if b in self.blocks), self.parsed_dir
        )
        for warning in section_warnings(section, list(summaries.values())):
            if warning.code in _PASSTHROUGH_QUALITY_CODES:
                self._warn(warning.code, f"{warning.message} (rendered: {source})", section.id)

    # -- markdown + assets --------------------------------------------------------

    def _markdown(
        self,
        text: str,
        section_id: str,
        counter: AssetCounter,
        source: str | None,
        block_id: str | None = None,
    ) -> str:
        tokens = self._md.parse(text)
        self._rewrite_images(tokens, section_id, self._parsed_bases(), counter, source, block_id)
        return str(self._md.renderer.render(tokens, self._md.options, {}))


def _first_heading_text(tokens: list[Token]) -> str | None:
    """The plain text of a leading heading (``# *Giới thiệu*`` → ``Giới thiệu``).

    Used as a TOC/report title, so Markdown syntax is dropped: only text and code
    spans are kept, and line breaks become spaces.
    """

    if len(tokens) < 2 or tokens[0].type != "heading_open" or tokens[1].type != "inline":
        return None
    parts = []
    for child in tokens[1].children or []:
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
    return " ".join("".join(parts).split()) or None


def _shift_headings(tokens: list[Token], level: int) -> None:
    """Re-level headings so the artifact's top heading sits at the section's level."""

    levels = [int(t.tag[1]) for t in tokens if t.type in {"heading_open", "heading_close"}]
    if not levels:
        return
    offset = max(1, min(level, 6)) - min(levels)
    for token in tokens:
        if token.type in {"heading_open", "heading_close"}:
            token.tag = f"h{max(1, min(int(token.tag[1]) + offset, 6))}"


def _heading(level: int, title: str) -> str:
    tag = f"h{max(1, min(level, 6))}"
    return f"<{tag}>{escape(title)}</{tag}>"


def _section_html(section: Section, source: SectionSource, body: str) -> str:
    level = max(1, min(section.level, 6))
    return (
        f'<section class="section level-{level} source-{source}" id="{escape(section.id)}" '
        f'data-source="{source}">{body}</section>\n'
    )


_STYLE = """
@page { size: A4; margin: 22mm 20mm 24mm 20mm;
  @bottom-center { content: counter(page); font-size: 9pt; color: #666; } }
html { font-family: "Noto Serif", "Source Serif 4", "DejaVu Serif", "Times New Roman", serif;
  font-size: 11pt; line-height: 1.5; color: #111; }
body { margin: 0; }
h1, h2, h3, h4, h5, h6 { font-family: "Noto Sans", "Source Sans 3", "DejaVu Sans",
  "Helvetica Neue", Arial, sans-serif; line-height: 1.25; break-after: avoid; }
.frontmatter { break-after: page; }
.frontmatter dl { display: grid; grid-template-columns: max-content 1fr; gap: 2pt 12pt; }
.frontmatter dt { font-weight: bold; }
.frontmatter dd { margin: 0; }
.notice { color: #555; font-size: 9.5pt; }
.toc { break-after: page; }
.toc ol { list-style: none; padding-left: 0; }
.toc li { margin: 1pt 0; }
.toc a { color: inherit; text-decoration: none; }
.toc .status { color: #888; font-size: 9pt; }
.section.level-1 { break-before: page; }
.source-note, .placeholder { color: #8a5a00; font-size: 9pt; font-style: italic; }
.source-skipped .placeholder { border: 1px dashed #c9a24a; padding: 6pt; }
figure { margin: 10pt 0; text-align: center; break-inside: avoid; }
figure img, p img { max-width: 100%; max-height: 220mm; }
figcaption { font-size: 9pt; color: #444; margin-top: 3pt; }
.missing-asset { display: inline-block; border: 1px dashed #b00; color: #b00;
  padding: 4pt 6pt; font-size: 9pt; }
.equation { font-family: "DejaVu Sans Mono", Menlo, monospace; font-size: 9.5pt;
  margin: 6pt 0; white-space: pre-wrap; }
table { border-collapse: collapse; margin: 8pt 0; font-size: 9.5pt; }
th, td { border: 1px solid #999; padding: 2pt 5pt; vertical-align: top; }
pre, code { font-family: "DejaVu Sans Mono", Menlo, monospace; font-size: 9pt; }
pre { white-space: pre-wrap; background: #f5f5f5; padding: 6pt; }
"""

# Self-contained page: images are data: URIs and nothing else may load or run, so a
# script or remote reference inside a translation artifact stays inert in any viewer.
_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"

_STATUS_LABELS = {"original": "original", "skipped": "skipped"}

# Shown under the heading of a translated section whose freshness is not ``fresh``,
# like the "Untranslated — original text" label of a fallback section.
_FRESHNESS_NOTES = {
    "stale": '<p class="source-note">Translation may be outdated — the original section '
    "changed after it was translated</p>",
    "untracked": '<p class="source-note">Translation status unknown — it may be outdated</p>',
}

# TOC markers for the same states, so the overview shows every non-fresh translation.
_FRESHNESS_LABELS = {"stale": "may be outdated", "untracked": "not tracked"}


def _document_html(report: ExportReport, bodies: list[str]) -> str:
    percent = f"{report.coverage * 100:.1f}%"
    details = (
        (
            "<dl>"
            f"<dt>doc_id</dt><dd>{escape(report.doc_id)}</dd>"
            f"<dt>language</dt><dd>{escape(report.lang)}</dd>"
            f"<dt>generated</dt><dd>{escape(report.generated_at)}</dd>"
            f"<dt>coverage</dt><dd>{report.translated_sections}/{report.total_sections} "
            f"sections translated ({percent})</dd>"
            f"<dt>fallback</dt><dd>{escape(report.fallback)}</dd>"
            "</dl>"
            '<p class="notice">Generated by BookGraph from section-level artifacts. A reading '
            "edition in progress — not a reproduction of the original page layout.</p>"
        )
        if report.show_status
        else ""
    )
    frontmatter = f'<header class="frontmatter"><h1>{escape(report.title)}</h1>{details}</header>\n'
    toc_items = []
    for entry in report.sections:
        indent = (max(1, min(entry.level, 6)) - 1) * 12
        status = _STATUS_LABELS.get(entry.source)
        if entry.freshness is not None:
            status = _FRESHNESS_LABELS.get(entry.freshness, status)
        if not report.show_status:
            status = None
        marker = f' <span class="status">({status})</span>' if status else ""
        toc_items.append(
            f'<li style="padding-left: {indent}pt"><a href="#{escape(entry.section_id)}">'
            f"{escape(entry.title)}</a>{marker}</li>"
        )
    toc = '<nav class="toc"><h2>Contents</h2><ol>' + "".join(toc_items) + "</ol></nav>\n"
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="{escape(report.lang)}">\n<head>\n<meta charset="utf-8">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{_CSP}">\n'
        f'<meta name="generator" content="bookgraph export translated-pdf">\n'
        f"<title>{escape(report.title)}</title>\n<style>{_STYLE}</style>\n</head>\n<body>\n"
        + frontmatter
        + toc
        + "<main>\n"
        + "".join(bodies)
        + "</main>\n</body>\n</html>\n"
    )


def report_path_for(output: Path) -> Path:
    """The report written beside an export: ``<name>.report.json``."""

    return output.with_name(output.stem + ".report.json")


def write_translated_export(
    export: TranslatedExport,
    output: Path,
    renderer: ExportRenderer,
    *,
    strict: bool = False,
) -> ExportReport:
    """Render ``export`` to ``output`` and write its report beside it.

    With ``strict`` any missing, remote, or unsupported asset, stale translation, or
    prose-only translation of a section with assets aborts before anything is
    written. The output is rendered to a temporary sibling and moved into place,
    so a failed render never leaves a truncated file at ``output``.
    """

    if strict and export.report.strict_warnings:
        raise ExportError(
            f"{len(export.report.strict_warnings)} problem(s) in strict mode: "
            + "; ".join(w.message for w in export.report.strict_warnings)
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.partial")
    try:
        renderer.render(export.html, partial)
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
    report = export.report.model_copy(update={"renderer": renderer.name, "output": str(output)})
    report_path_for(output).write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report
