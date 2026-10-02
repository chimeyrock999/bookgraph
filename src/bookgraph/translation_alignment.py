"""Block-aligned translations: map a translation body back to its source blocks.

A writer may submit a translation as units — Markdown plus the ids of the section's
``CanonicalBlock``s it translates (:class:`~bookgraph.models.TranslationUnit`). The
units are joined into the usual Markdown body, and the registry sidecar records each
unit's span of that body (:class:`~bookgraph.models.AlignedUnit`), so a bilingual
export can set each source paragraph beside its own translation.

An alignment is checked against the section (:func:`check_alignment`):

- every unit has content and at least one block id (``empty_unit``);
- every block id belongs to the section (``foreign_block``);
- units follow the source order: the ids within a unit strictly increase, and a unit
  starts no earlier than the previous one ends; repeating the boundary block is a
  split (``out_of_order``);
- the spans tile the body in order (``bad_range``; only a stored alignment can break
  this, after the body or the section changed);
- a source text block that no unit references is a gap (``unaligned_block``), a
  warning only. Headings, figures, tables, equations and code are passed through
  untranslated, so leaving them out is not a gap.

The section content hash and staleness rules ignore the alignment: it is provenance,
not content. See ``docs/cli/artifacts.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from bookgraph.models import (
    AlignedUnit,
    AlignmentStatus,
    CanonicalBlock,
    Section,
    TranslationAlignmentIssue,
    TranslationUnit,
)
from bookgraph.translations import TranslationState, decoded_body

# Units are joined by one blank line, so each starts a Markdown block of its own.
UNIT_SEPARATOR = "\n\n"

# Source block types a translation is expected to cover; the rest pass through.
_TEXT_BLOCK_TYPES = frozenset({"text", "list", "unknown"})


@dataclass(frozen=True)
class AlignmentCheck:
    """A translation's alignment status against its section.

    ``units`` is the alignment when it is usable (``aligned``), else empty. ``issues``
    holds the gaps of an aligned translation, or the errors that make it ``invalid``.
    """

    status: AlignmentStatus
    units: list[AlignedUnit] = field(default_factory=list)
    issues: list[TranslationAlignmentIssue] = field(default_factory=list)


def join_units(units: Sequence[TranslationUnit]) -> tuple[str, list[AlignedUnit]]:
    """The Markdown body of ``units`` and each unit's span in it.

    Each unit's content is trimmed of surrounding blank lines; the body ends with a
    newline that no span covers.
    """

    parts: list[str] = []
    aligned: list[AlignedUnit] = []
    offset = 0
    for index, unit in enumerate(units):
        if index:
            parts.append(UNIT_SEPARATOR)
            offset += len(UNIT_SEPARATOR)
        content = unit.content.strip("\r\n")
        parts.append(content)
        aligned.append(
            AlignedUnit(
                source_block_ids=list(unit.source_block_ids),
                start=offset,
                end=offset + len(content),
            )
        )
        offset += len(content)
    return "".join(parts) + "\n", aligned


def check_alignment(
    section: Section,
    blocks: Mapping[str, CanonicalBlock],
    units: Sequence[AlignedUnit],
    body: str,
) -> list[TranslationAlignmentIssue]:
    """Every problem with ``units`` as an alignment of ``body`` to ``section``.

    ``blocks`` are the parsed document's blocks by id; a block missing from it (a
    sections-only workspace) is never reported as a gap.
    """

    position = {block_id: index for index, block_id in enumerate(section.block_ids)}
    issues: list[TranslationAlignmentIssue] = []
    previous_end = 0
    previous_last = -1
    referenced: set[str] = set()
    for index, unit in enumerate(units):
        if not unit.source_block_ids or not body[unit.start : unit.end].strip():
            issues.append(
                TranslationAlignmentIssue(
                    code="empty_unit",
                    unit=index,
                    message=f"unit {index} needs content and at least one source block id",
                )
            )
        if not 0 <= previous_end <= unit.start <= unit.end <= len(body) or (
            body[previous_end : unit.start].strip()
        ):
            issues.append(
                TranslationAlignmentIssue(
                    code="bad_range",
                    unit=index,
                    message=f"unit {index} span {unit.start}:{unit.end} does not follow "
                    "the previous unit in the body",
                )
            )
        previous_end = max(previous_end, unit.end)
        last = -1
        for block_id in unit.source_block_ids:
            if block_id not in position:
                issues.append(
                    TranslationAlignmentIssue(
                        code="foreign_block",
                        unit=index,
                        block_id=block_id,
                        message=f"unit {index} references block {block_id}, which is not "
                        f"in section {section.id}",
                    )
                )
                continue
            referenced.add(block_id)
            current = position[block_id]
            # The first id may repeat the previous unit's last block (a split).
            floor = previous_last if last < 0 else last + 1
            if current < floor:
                issues.append(
                    TranslationAlignmentIssue(
                        code="out_of_order",
                        unit=index,
                        block_id=block_id,
                        message=f"unit {index} references block {block_id} out of source order",
                    )
                )
            last = max(last, current)
        previous_last = max(previous_last, last)
    if body[previous_end:].strip():
        issues.append(
            TranslationAlignmentIssue(
                code="bad_range",
                message=f"the body continues after the last unit (offset {previous_end})",
            )
        )
    issues.extend(_gaps(section, blocks, referenced))
    return issues


def _gaps(
    section: Section, blocks: Mapping[str, CanonicalBlock], referenced: set[str]
) -> list[TranslationAlignmentIssue]:
    gaps: list[TranslationAlignmentIssue] = []
    for block_id in section.block_ids:
        block = blocks.get(block_id)
        if (
            block_id in referenced
            or block is None
            or block.type not in _TEXT_BLOCK_TYPES
            or block.metadata.get("code")
            or not block.text.strip()
        ):
            continue
        gaps.append(
            TranslationAlignmentIssue(
                code="unaligned_block",
                block_id=block_id,
                message=f"source {block.type} block {block_id} is not translated by any unit",
            )
        )
    return gaps


def alignment_errors(
    issues: Sequence[TranslationAlignmentIssue],
) -> list[TranslationAlignmentIssue]:
    """The issues that make an alignment invalid (everything but gaps)."""

    return [issue for issue in issues if issue.code != "unaligned_block"]


def describe_alignment_issues(issues: Sequence[TranslationAlignmentIssue]) -> str:
    """One line listing ``issues`` for an error or warning message."""

    return "; ".join(issue.message for issue in issues)


def alignment_check(
    units: Sequence[AlignedUnit] | None,
    section: Section | None,
    blocks: Mapping[str, CanonicalBlock],
    body: str | None,
) -> AlignmentCheck:
    """The alignment status of a stored translation.

    ``units`` is the sidecar's alignment (``None`` when unaligned); ``body`` the decoded
    body it was registered with. An alignment that no longer fits the section (a
    re-segment moved its blocks) or the body is ``invalid``, and is not used.
    """

    if units is None or section is None or body is None:
        return AlignmentCheck(status="unaligned")
    issues = check_alignment(section, blocks, units, body)
    errors = alignment_errors(issues)
    if errors:
        return AlignmentCheck(status="invalid", issues=errors)
    return AlignmentCheck(status="aligned", units=list(units), issues=issues)


def translation_alignment(
    state: TranslationState,
    section: Section | None,
    blocks: Mapping[str, CanonicalBlock],
) -> AlignmentCheck:
    """A cached translation's block alignment, checked against the live section.

    The one entry point the translation tools, reading-batch completion and the
    bilingual export share. Only a registered body has an alignment: an untracked body
    (no sidecar vouching for it) is ``unaligned``.
    """

    units = state.artifact.alignment if state.artifact is not None else None
    return alignment_check(units, section, blocks, decoded_body(state))
