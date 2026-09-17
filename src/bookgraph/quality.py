"""Deterministic data-quality checks over segmented sections and parsed assets.

Two stages consume these checks and must agree on their codes and thresholds:

- the **segment stage** writes a per-document report
  (``sources/sections/<doc_id>/quality.json``) so an ingest run surfaces anomalies
  such as an inverted page span immediately, rather than at reading time;
- the **MCP section APIs** attach the per-section warnings to every
  ``SectionView``, so a reading agent learns that a section's page range is broken
  or that its prose is only asset captions without inspecting
  ``sources/parsed/<doc_id>/document.json`` by hand.

Everything here is pure and deterministic: same sections plus same blocks, same
warnings. Keeping it in one module is what stops the ingest report and the
reading APIs from drifting into two different definitions of "suspicious" — and
both sides decide whether an asset exists through the one resolver in
:mod:`bookgraph.assets`, so a reference the reader cannot open is reported as
``asset_file_missing`` by ingest and by ``get_section`` alike, instead of counting
as an asset on one side and vanishing on the other.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from bookgraph.assets import asset_reference, resolve_asset_path
from bookgraph.models import ASSET_BLOCK_TYPES, CanonicalBlock, Section

# Report filename, written by the segment stage next to ``sections.jsonl``.
QUALITY_REPORT_NAME = "quality.json"

# Warning codes. They are part of the artifact/API contract (see
# ``docs/cli/artifacts.md``), so treat them as stable strings, not display text.
PAGE_RANGE_INVERTED = "page_range_inverted"
PAGE_RANGE_INCOMPLETE = "page_range_incomplete"
ASSET_TYPE_AMBIGUOUS = "asset_type_ambiguous"
ASSET_CAPTIONS_ONLY = "asset_captions_only"
ASSET_TEXT_SPARSE = "asset_text_sparse"
ASSET_FILE_MISSING = "asset_file_missing"

# Confidence in a parser's image/table/chart classification for one asset block.
# The caption is the only independent signal available without opening the file,
# so the scale has three rungs: the caption label agrees with the parser, there is
# no label to compare against, or the label contradicts the parser.
ASSET_CONFIDENCE_CORROBORATED = 1.0
ASSET_CONFIDENCE_UNLABELLED = 0.8
ASSET_CONFIDENCE_CONFLICTING = 0.4

# Prose length (after the asset captions are removed) below which a section with
# assets is reported. ``_CAPTION_ONLY`` means "nothing but captions survived";
# ``_SPARSE`` means "a figure/table-heavy section with barely any prose around it",
# which takes several assets — one figure beside a short but genuine paragraph is
# ordinary book prose, not an anomaly.
_CAPTION_ONLY_RESIDUAL_CHARS = 15
_SPARSE_RESIDUAL_CHARS = 200
_SPARSE_MIN_ASSETS = 2

# Caption labels, anchored at the start of the caption where real figure/table
# labels live ("Figure 3-1. …", "**Table 2**: …"). Anchoring is deliberate: a
# passing mention ("as the table above shows") must not be read as a label.
_FIGURE_CAPTION_RE = re.compile(
    r"^\W*(?:figure|fig|diagram|illustration|image|photo|plate|chart|graph)\b",
    re.IGNORECASE,
)
_TABLE_CAPTION_RE = re.compile(r"^\W*(?:table|tbl)\b", re.IGNORECASE)

# Which parser block types each caption label corroborates. A "chart" block is a
# figure as far as a caption label is concerned, so "Figure 4" agrees with it.
_TYPES_FOR_LABEL: dict[str, frozenset[str]] = {
    "image": frozenset({"image", "chart"}),
    "table": frozenset({"table"}),
}


@dataclass(frozen=True)
class AssetSummary:
    """The minimum an asset contributes to a quality check.

    Built from parsed ``CanonicalBlock``s at ingest time and from resolved
    ``AssetRef``s in the MCP service, so both sides run the same checks.

    ``resolved`` is whether the referenced file actually exists under the parsed
    document directory (see :func:`bookgraph.assets.resolve_asset_path`). An
    unresolved asset still counts as an asset — its caption is in the section text and
    its content is not — but it is reported as missing rather than as something the
    reader can open.
    """

    block_id: str
    type: str
    caption: str = ""
    resolved: bool = True


@dataclass(frozen=True)
class AssetClassification:
    """A parser's asset type, corroborated or contradicted by its caption label.

    ``suggested_type`` is set **only** when the caption contradicts the parser, so
    it doubles as "this classification is disputed"; a client can surface the
    correction (or re-classify) without re-deriving the caption heuristics.
    """

    type: str
    confidence: float
    suggested_type: str | None = None
    reason: str = ""


class SectionWarning(BaseModel):
    """One quality anomaly in a section (or in one of the section's assets).

    ``code`` is the stable machine-readable kind; ``message`` is the human/agent
    sentence. ``block_id`` is set for an asset-scoped warning and ``None`` for a
    section-scoped one.
    """

    code: str
    message: str
    block_id: str | None = None


class DocumentWarning(BaseModel):
    """A :class:`SectionWarning` tagged with the section it was found in."""

    section_id: str
    code: str
    message: str
    block_id: str | None = None


class QualityReport(BaseModel):
    """Per-document ingest quality report written beside ``sections.jsonl``.

    Deterministic by design (no timestamps), so re-running ``bookgraph segment``
    on unchanged input rewrites a byte-identical report.
    """

    doc_id: str
    section_count: int
    warning_count: int
    warning_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[DocumentWarning] = Field(default_factory=list)


def classify_asset(block_type: str, caption: str) -> AssetClassification:
    """Score a parser's asset type against the label its caption carries.

    MinerU (and any layout model) does confuse figures with tables — dogfooding
    found a figure emitted as a ``table`` block (issue #38). The caption is the one
    corroborating signal available without opening the file, so a mismatch lowers
    the confidence and proposes the caption's type instead of silently trusting
    either side.
    """

    label = _caption_label(caption)
    if label is None:
        return AssetClassification(
            type=block_type,
            confidence=ASSET_CONFIDENCE_UNLABELLED,
            reason="caption carries no figure/table label to corroborate the parser's type",
        )
    if block_type in _TYPES_FOR_LABEL[label]:
        return AssetClassification(
            type=block_type,
            confidence=ASSET_CONFIDENCE_CORROBORATED,
            reason=f"caption label agrees with the parser's '{block_type}' type",
        )
    return AssetClassification(
        type=block_type,
        confidence=ASSET_CONFIDENCE_CONFLICTING,
        suggested_type=label,
        reason=(
            f"caption reads as a '{label}' but the parser classified the block "
            f"as '{block_type}'"
        ),
    )


def section_warnings(
    section: Section, assets: Sequence[AssetSummary] = ()
) -> list[SectionWarning]:
    """Return every quality warning for one section, in a stable order.

    Page-range checks need only the section, so they run even when a caller has no
    assets to pass (an unparsed document, or ``include_assets=False`` on the MCP
    section APIs); asset checks are simply skipped in that case.
    """

    return _page_range_warnings(section) + _asset_warnings(section, assets)


def asset_summaries(
    blocks: Iterable[CanonicalBlock], parsed_dir: Path | None = None
) -> dict[str, AssetSummary]:
    """Index the parsed blocks that carry a real image/table asset, by block id.

    Only blocks with an asset *reference* count: a markdown table is an asset-typed
    block whose content is rendered inline into the section text, and treating it as
    a figure would report the section as "captions only" when its text is in fact
    the whole table.

    Pass ``parsed_dir`` (the document's ``sources/parsed/<doc_id>/``) to resolve each
    reference against the files the parser actually staged, exactly as the MCP section
    APIs do. Without it every reference is assumed resolvable, which is right only when
    the caller has no parsed directory to check against.
    """

    return {
        block.id: AssetSummary(
            block_id=block.id,
            type=block.type,
            caption=block.text,
            resolved=parsed_dir is None or resolve_asset_path(parsed_dir, block) is not None,
        )
        for block in blocks
        if block.type in ASSET_BLOCK_TYPES and asset_reference(block)
    }


def document_quality_report(
    doc_id: str,
    sections: Sequence[Section],
    blocks: Iterable[CanonicalBlock] = (),
    parsed_dir: Path | None = None,
) -> QualityReport:
    """Check every section of a document and aggregate the warnings into a report.

    ``parsed_dir`` is the document's ``sources/parsed/<doc_id>/``; pass it so asset
    references are resolved against the staged files the reader will actually get.
    """

    summaries = asset_summaries(blocks, parsed_dir)
    warnings: list[DocumentWarning] = []
    for section in sections:
        section_assets = [
            summaries[block_id] for block_id in section.block_ids if block_id in summaries
        ]
        warnings.extend(
            DocumentWarning(section_id=section.id, **warning.model_dump())
            for warning in section_warnings(section, section_assets)
        )
    counts = Counter(warning.code for warning in warnings)
    return QualityReport(
        doc_id=doc_id,
        section_count=len(sections),
        warning_count=len(warnings),
        warning_counts=dict(sorted(counts.items())),
        warnings=warnings,
    )


def write_quality_report(report: QualityReport, output_dir: Path) -> Path:
    """Write ``quality.json`` into a document's sections directory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / QUALITY_REPORT_NAME
    path.write_text(report.model_dump_json(indent=2) + "\n")
    return path


def read_quality_report(path: Path) -> QualityReport:
    """Load a ``quality.json`` report written by :func:`write_quality_report`."""

    return QualityReport.model_validate_json(path.read_text())


def _page_range_warnings(section: Section) -> list[SectionWarning]:
    """Report page spans that cannot be true of a real reading unit.

    An inverted span (``page_start > page_end``) came from segmenters deriving
    ``page_end`` from the *next* section's start page, which underflows when two
    sections begin on the same page. The segmenters now clamp it, so a surviving
    warning points at genuinely bad upstream page data (e.g. PDF bookmarks whose
    page indices do not follow the reading order).
    """

    start, end = section.page_start, section.page_end
    if start is not None and end is not None and start > end:
        return [
            SectionWarning(
                code=PAGE_RANGE_INVERTED,
                message=(
                    f"page range is inverted: page_start={start} > page_end={end}; "
                    "the page provenance for this section is unreliable"
                ),
            )
        ]
    if (start is None) != (end is None):
        return [
            SectionWarning(
                code=PAGE_RANGE_INCOMPLETE,
                message=(
                    f"page range is half-known: page_start={start}, page_end={end}; "
                    "only part of this section's page provenance survived parsing"
                ),
            )
        ]
    return []


def _asset_warnings(
    section: Section, assets: Sequence[AssetSummary]
) -> list[SectionWarning]:
    """Report disputed asset types, and prose that is dwarfed by its assets."""

    warnings: list[SectionWarning] = []
    for asset in assets:
        if not asset.resolved:
            # The file the section points at was never staged, so neither the prose nor
            # the asset carries its content. Report that instead of a type dispute the
            # reader could not settle by opening the file anyway.
            warnings.append(
                SectionWarning(
                    code=ASSET_FILE_MISSING,
                    message=(
                        f"asset {asset.block_id} references a file that is not available "
                        "under the parsed document directory (never staged, remote, or "
                        "outside the workspace); its content is lost to the reader"
                    ),
                    block_id=asset.block_id,
                )
            )
            continue
        classification = classify_asset(asset.type, asset.caption)
        if classification.suggested_type is None:
            continue
        warnings.append(
            SectionWarning(
                code=ASSET_TYPE_AMBIGUOUS,
                message=(
                    f"asset {asset.block_id}: {classification.reason}; treat it as "
                    f"'{classification.suggested_type}' or open the file to confirm"
                ),
                block_id=asset.block_id,
            )
        )
    if not assets:
        return warnings

    residual = len(_text_beyond_captions(section.text, assets))
    if residual <= _CAPTION_ONLY_RESIDUAL_CHARS:
        warnings.append(
            SectionWarning(
                code=ASSET_CAPTIONS_ONLY,
                message=(
                    f"this section has {len(assets)} image/table asset(s) and its text is "
                    "effectively just their captions; the labels or tabular data live "
                    "inside the file(s) — inspect the asset(s), not just `text`"
                ),
            )
        )
    elif residual < _SPARSE_RESIDUAL_CHARS and len(assets) >= _SPARSE_MIN_ASSETS:
        warnings.append(
            SectionWarning(
                code=ASSET_TEXT_SPARSE,
                message=(
                    f"this section has {len(assets)} image/table asset(s) but only "
                    f"{residual} characters of prose beyond their captions; the "
                    "figure/table content is likely the substance here"
                ),
            )
        )
    return warnings


def _text_beyond_captions(text: str, assets: Sequence[AssetSummary]) -> str:
    """Strip each asset caption from the section body **once**, longest caption first.

    Removing once, rather than every occurrence, keeps a short or generic caption
    like ``"1"`` or ``"Table"`` from being wiped out of genuine prose and turning a
    well-written section into a false warning.

    Longest first, because captions overlap: with ``"Table"`` and ``"Table 2 data"``,
    stripping the short one first eats the head of the long one, whose own strip then
    finds nothing and leaves fragments behind — inflating the residual until a
    genuinely caption-only section slips past the check on both surfaces at once.
    """

    residual = text.strip()
    for asset in sorted(assets, key=lambda asset: len(asset.caption.strip()), reverse=True):
        caption = asset.caption.strip()
        if caption:
            residual = residual.replace(caption, "", 1)
    return residual.strip()


def _caption_label(caption: str) -> str | None:
    """Return the block type a caption's leading label implies, if it has one."""

    if _FIGURE_CAPTION_RE.match(caption):
        return "image"
    if _TABLE_CAPTION_RE.match(caption):
        return "table"
    return None

