"""Assemble a partially translated book into one reading edition (HTML, then PDF).

The original sections manifest is the skeleton, arranged into the book's structure
by :mod:`bookgraph.exports.outline` (the source PDF outline's order and hierarchy when
there is one, else ``sections.jsonl`` order): child sections render inside their
chapter, and each chapter opens a new page; :mod:`bookgraph.exports.render` lays the
book out as HTML. Each section is filled from its translation in the translation
registry (:mod:`bookgraph.translations`) for the requested language, or — per the
fallback policy — from the original parsed content, a placeholder, or not at all. The
export only reads the registry: a translation's freshness (``fresh`` / ``stale`` /
``untracked``) is reported per section in the report.

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
section, the reference, and the file that carries it. ``bilingual`` mode sets each
section beside its original (:mod:`.bilingual`).

This is a clean reading edition, not a pixel-perfect reconstruction of the
publisher's layout.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from markdown_it import MarkdownIt

from bookgraph.assets import asset_reference, resolve_asset_path
from bookgraph.books import read_book_bookmarks
from bookgraph.documents import read_document
from bookgraph.exports.bilingual import block_marker, mark_units
from bookgraph.exports.edition import TranslatedExport
from bookgraph.exports.images import AssetCounter, AssetOrigin, ImageEmbedder
from bookgraph.exports.links import resolve_section_links
from bookgraph.exports.models import (
    ASSET_MISSING,
    TRANSLATION_EMPTY,
    TRANSLATION_MISSING_ASSETS,
    TRANSLATION_STALE,
    TRANSLATION_STRUCTURE_CHANGED,
    TRANSLATION_UNREADABLE,
    TRANSLATION_UNTRACKED,
    BilingualLayout,
    ExportMode,
    ExportReport,
    ExportSection,
    FallbackPolicy,
    SectionSource,
    TranslationFreshness,
)
from bookgraph.exports.outline import OutlineNode, build_outline, flatten
from bookgraph.exports.render import (
    FRESHNESS_NOTES,
    SKIPPED_NOTE,
    UNTRANSLATED_NOTE,
    heading,
)
from bookgraph.exports.renderers import ExportWriter
from bookgraph.exports.tokens import first_heading_text, shift_headings
from bookgraph.models import ASSET_BLOCK_TYPES, CanonicalBlock, Section
from bookgraph.quality import (
    ASSET_CAPTIONS_ONLY,
    ASSET_TEXT_SPARSE,
    asset_summaries,
    section_warnings,
)
from bookgraph.sections import read_sections
from bookgraph.translation_alignment import (
    AlignmentCheck,
    markdown_parser,
    translation_alignment,
)
from bookgraph.translation_assets import check_translation_assets, translation_link_bases
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
    mode: ExportMode = "translated",
    generated_at: str | None = None,
    show_status: bool = False,
    bilingual_layout: BilingualLayout = "columns",
    source_lang: str | None = None,
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

    ``mode="bilingual"`` puts the original beside each section (see ``bilingual``).
    ``show_status`` prints status/debug metadata (freshness and fallback notes, TOC
    status markers, coverage, missing-asset placeholders) on the reading pages; by
    default it is only in the report. ``bilingual_layout`` is the output format's
    pairing (``interleaved`` for EPUB) and ``source_lang`` the original's language tag.
    """

    try:
        validate_slug_id(doc_id, field_name="doc_id")
        lang = validate_lang(lang)
        source_lang = validate_lang(source_lang) if source_lang is not None else None
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
    counts = Counter(section.id for section in sections)
    duplicates = sorted(section_id for section_id, count in counts.items() if count > 1)
    if duplicates:
        # Section ids are the export's anchors and outline keys.
        raise ExportError(
            f"Invalid sections manifest: {manifest}: duplicate section ids: "
            + ", ".join(duplicates)
        )

    parsed_dir = workspace.sources_parsed / doc_id
    title, blocks = _load_document(parsed_dir, doc_id)
    assembler = _Assembler(
        workspace=workspace,
        parsed_dir=parsed_dir,
        blocks=blocks,
        manifest=manifest,
        show_status=show_status,
        align_rows=mode == "bilingual",
    )

    outline = build_outline(sections, read_book_bookmarks(workspace, doc_id))
    parents = {
        child.section.id: node.section.id for node in flatten(outline) for child in node.children
    }
    rendered = {
        node.section.id: assembler.render_section(
            node,
            parents.get(node.section.id),
            translation_state(
                workspace, lang, doc_id, node.section.id, section_content_hash(node.section)
            ),
            fallback,
        )
        for node in flatten(outline)
    }
    originals = (
        {node.section.id: assembler.original_column(node) for node in flatten(outline)}
        if mode == "bilingual"
        else {}
    )
    rendered, link_warnings = resolve_section_links(
        outline,
        rendered,
        originals,
        assembler.original_source,
        lang,
        assembler.alignments,
        layout=bilingual_layout,
        source_lang=source_lang,
    )
    assembler.warnings.extend(link_warnings)
    report = ExportReport.from_sections(
        [entry for entry, _ in rendered.values()],
        mode=mode,
        doc_id=doc_id,
        title=title,
        lang=lang,
        fallback=fallback,
        generated_at=generated_at or default_generated_at(),
        warnings=assembler.warnings,
        show_status=show_status,
        source_lang=source_lang,
    )
    # Checked after rendering, not on artifact existence: an empty or unreadable
    # artifact falls back too, and must count as untranslated under ``fail``. Stale and
    # untracked translations are rendered, so they count as translated here; they are
    # flagged by warnings instead (and ``--strict`` refuses stale ones).
    if fallback == "fail" and report.untranslated:
        raise UntranslatedSectionsError(report)
    bodies = {section_id: body for section_id, (_, body) in rendered.items()}
    sources = {
        node.section.id: assembler.original_source(node.section) for node in flatten(outline)
    }
    return TranslatedExport(report, outline, bodies, bilingual_layout, sources)


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
    # Bilingual mode: mark units and source blocks so rows can follow the alignment,
    # recorded by section id (:mod:`.bilingual`).
    align_rows: bool = False
    alignments: dict[str, AlignmentCheck] = field(default_factory=dict)
    # Rendered originals by section id: a bilingual fallback row renders (and warns) once.
    _originals: dict[str, tuple[str, AssetCounter]] = field(default_factory=dict)
    _md: MarkdownIt = field(default_factory=markdown_parser)

    # -- sections -----------------------------------------------------------------

    def render_section(
        self,
        node: OutlineNode,
        parent_id: str | None,
        state: TranslationState,
        fallback: FallbackPolicy,
    ) -> tuple[ExportSection, str]:
        """The section's report entry and its own HTML (heading + body, no children)."""

        section, depth = node.section, node.depth
        counter = AssetCounter()
        translated = self._translated_body(section, depth, state, counter)
        if translated is not None:
            # A body was read, so the status is fresh, stale, or untracked (never
            # missing, and never orphaned: the section exists).
            freshness: TranslationFreshness = (
                state.status if state.status in ("fresh", "stale") else "untracked"
            )
            title, heading_html, body = translated
            self._freshness_warnings(section, state, freshness)
            self._quality_warnings(section, source="translated")
            entry = self._entry(
                node, parent_id, title, "translated", state.paths.body, counter, freshness
            )
            note = FRESHNESS_NOTES.get(freshness, "") if self.show_status else ""
            return entry, heading_html + note + body
        if fallback == "skip":
            # The reader sees the heading only (the chapter's structure stays whole);
            # the report — and ``show_status`` — say the content was left out.
            body = heading(depth, section.title) + (SKIPPED_NOTE if self.show_status else "")
            return self._entry(node, parent_id, section.title, "skipped", None, counter), body
        body = (
            heading(depth, section.title)
            + (UNTRANSLATED_NOTE if self.show_status else "")
            + self._original_body(section, depth, counter)
        )
        self._quality_warnings(section, source="original")
        return self._entry(node, parent_id, section.title, "original", None, counter), body

    def original_column(self, node: OutlineNode) -> tuple[str, int, int]:
        """The bilingual left column under its source title, with its asset counts.

        It takes the outline depth, like the mixed column, so both headings match.
        Warnings raised here are tagged ``column="original"``. Every mixed rendering is
        built first, so a fallback row's original (cached, warned once) is already
        tagged ``mixed``: only problems ``--mode translated`` would not show get here.
        """

        section, counter = node.section, AssetCounter()
        self.column = "original"
        try:
            body = heading(node.depth, section.title) + self._original_body(
                section, node.depth, counter
            )
        finally:
            self.column = "mixed"
        return body, counter.embedded, counter.missing

    def _entry(
        self,
        node: OutlineNode,
        parent_id: str | None,
        title: str,
        source: SectionSource,
        artifact: Path | None,
        counter: AssetCounter,
        freshness: TranslationFreshness | None = None,
    ) -> ExportSection:
        return ExportSection(
            section_id=node.section.id,
            title=title,
            level=node.section.level,
            depth=node.depth,
            parent_id=parent_id,
            source=source,
            artifact=_relative(self.workspace, artifact),
            freshness=freshness,
            assets_embedded=counter.embedded,
            assets_missing=counter.missing,
        )

    def _translated_body(
        self, section: Section, depth: int, state: TranslationState, counter: AssetCounter
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
                    content="translation",
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
                content="translation",
            )
            return None
        frontmatter, body = split_frontmatter(raw)
        if not body.strip():
            self._warn(
                TRANSLATION_EMPTY,
                f"translation artifact {relative} is empty; using the fallback policy instead",
                section.id,
                content="translation",
            )
            return None
        self._structure_warnings(section, body, artifact)
        self._asset_warnings(section, body, state)

        tokens = self._md.parse(body)
        heading_title = first_heading_text(tokens)
        shift_headings(tokens, depth)
        self._rewrite_images(
            tokens,
            section.id,
            self._translation_bases(artifact),
            counter,
            relative,
            content="translation",
        )
        fm_title = frontmatter.get("title")
        title = heading_title or (fm_title if isinstance(fm_title, str) and fm_title else None)
        if heading_title is None:
            heading_html = heading(depth, title or section.title)
        else:
            # Render the leading heading on its own so a freshness note can follow it.
            heading_end = next(i for i, t in enumerate(tokens) if t.type == "heading_close") + 1
            heading_html = self._md.renderer.render(tokens[:heading_end], self._md.options, {})
            tokens = tokens[heading_end:]
        if self.align_rows:
            check = translation_alignment(state, section, self.blocks)
            self.alignments[section.id] = check
            tokens = mark_units(tokens, body, check.units)
        html = self._md.renderer.render(tokens, self._md.options, {})
        return title or section.title, heading_html, html

    def _structure_warnings(self, section: Section, body: str, artifact: Path) -> None:
        """Flag a translation whose link destinations/anchors/paths differ from the source.

        Export navigation itself anchors on section ids, never on (translated) heading
        text, but intra-book links are resolved from their source destinations
        (:mod:`.links`), and image paths only work as written.
        """

        resolves = local_asset_resolver(self.workspace.root, self._translation_bases(artifact))
        issues = check_section_translation(
            section, body, blocks=self.blocks, asset_resolves=resolves
        )
        if issues:
            self._warn(
                TRANSLATION_STRUCTURE_CHANGED,
                f"translation {_relative(self.workspace, artifact)} changed structural "
                f"Markdown of its section: {describe_structure_issues(issues)}",
                section.id,
                content="translation",
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
                content="translation",
            )
        elif freshness == "untracked":
            self._warn(
                TRANSLATION_UNTRACKED,
                f"translation {relative} has no registry record (or was edited after "
                "registration), so its freshness is unknown; register it with "
                "write_section_translation",
                section.id,
                content="translation",
            )

    def _translation_bases(self, artifact: Path) -> list[Path]:
        return translation_link_bases(self.workspace.root, self.parsed_dir, artifact.parent)

    def _asset_warnings(self, section: Section, body: str, state: TranslationState) -> None:
        """Flag a translation that left out its section's figures/tables.

        The registry's reuse rule: a translation is complete only when it carried the
        section's figures/tables, or the section has none. The sidecar's
        ``includes_assets`` is not trusted for that: every staged asset must be linked
        from the body, whatever the sidecar says (and for an untracked body too). The
        claim only stands for assets whose file was never staged, which no body links;
        an untracked body has no claim, so those are not held against it.
        """

        check = check_translation_assets(
            section,
            body,
            blocks=self.blocks,
            root=self.workspace.root,
            parsed_dir=self.parsed_dir,
            body_dir=state.paths.body.parent,
        )
        relative = _relative(self.workspace, state.paths.body)
        document = _relative(self.workspace, self.parsed_dir / "document.json")
        for asset in check.missing:
            self._warn(
                TRANSLATION_MISSING_ASSETS,
                f"translation {relative} does not link the section's {asset.type} "
                f"{asset.block_id} ({asset.link}); it is not in the translated text",
                section.id,
                asset.link,
                AssetOrigin(document, asset.block_id),
                content="translation",
            )
        if (
            not check.missing
            and state.artifact is not None
            and not check.includes_assets(state.artifact.includes_assets)
        ):
            self._warn(
                TRANSLATION_MISSING_ASSETS,
                f"translation {relative} is prose-only (includes_assets=false) but the "
                "section has figures/tables; they are not in the translated text",
                section.id,
                content="translation",
            )

    def _original_body(self, section: Section, depth: int, counter: AssetCounter) -> str:
        # Warnings (with their source file/block origin) are raised on the first render.
        if section.id not in self._originals:
            own = AssetCounter()
            self._originals[section.id] = (self._render_original(section, depth, own), own)
        html, own = self._originals[section.id]
        counter.embedded += own.embedded
        counter.missing += own.missing
        return html

    def _render_original(self, section: Section, depth: int, counter: AssetCounter) -> str:
        blocks = [self.blocks[b] for b in section.block_ids if b in self.blocks]
        source = self.original_source(section)
        if not blocks:
            return self._markdown(section.text, section.id, counter, source)
        parts: list[str] = []
        for index, block in enumerate(blocks):
            if self.align_rows:
                parts.append(block_marker(block.id))
            if block.type == "title":
                if index == 0 and block.text.strip() == section.title.strip():
                    continue  # the section heading is already rendered
                level = max(block.level or depth + 1, depth + 1)
                parts.append(heading(level, block.text))
            elif block.type in ASSET_BLOCK_TYPES and asset_reference(block):
                parts.append(self._asset_block(block, section.id, counter, source))
            elif block.type == "equation":
                parts.append(f'<div class="equation">{escape(block.text)}</div>')
            elif block.text.strip():
                parts.append(self._markdown(block.text, section.id, counter, source, block.id))
        return "".join(parts)

    def original_source(self, section: Section) -> str | None:
        """The file an original section is rebuilt from: ``document.json``, else the
        sections manifest when none of its blocks were parsed."""

        if any(b in self.blocks for b in section.block_ids):
            return _relative(self.workspace, self.parsed_dir / "document.json")
        return _relative(self.workspace, self.manifest)

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


def report_path_for(output: Path) -> Path:
    """The report written beside an export: ``<name>.report.json``."""

    return output.with_name(output.stem + ".report.json")


def write_translated_export(
    export: TranslatedExport,
    output: Path,
    renderer: ExportWriter,
    *,
    strict: bool = False,
) -> ExportReport:
    """Write ``export`` to ``output`` and its report, with the writer's warnings, beside it.

    With ``strict`` any missing, remote, or unsupported asset, stale translation, or
    prose-only translation of a section with assets aborts before anything is
    written. The output is rendered to a temporary sibling and moved into place,
    so a failed render never leaves a truncated file at ``output``.
    """

    if export.report.mode == "bilingual" and export.bilingual_layout != renderer.bilingual_layout:
        raise ExportError(
            f"the export was laid out for {export.bilingual_layout} bilingual rows, but "
            f"{renderer.name} writes {renderer.bilingual_layout} ones"
        )
    if strict and export.report.strict_warnings:
        raise ExportError(
            f"{export.report.strict_summary()}: "
            + "; ".join(w.describe() for w in export.report.strict_warnings)
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.partial")
    try:
        warnings = renderer.write(export, partial)
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
    report = export.report.model_copy(
        update={
            "renderer": renderer.name,
            "output": str(output),
            "warnings": [*export.report.warnings, *warnings],
        }
    )
    report_path_for(output).write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report
