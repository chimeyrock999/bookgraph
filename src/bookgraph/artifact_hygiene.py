"""Keep operational chatter out of reusable reading artifacts.

Reading/translation jobs produce two kinds of text: the **artifact** (a translated
section, an annotation summary — book content, reused by later jobs and exported to
readers) and **diagnostics** (delivery markers, progress footers, QA/checker notes,
export status labels). Diagnostics belong in the chat reply, the job log, or a
report JSON; once they land in an artifact they get cached, re-served, and printed in
a reading PDF. This module recognises them so writers can refuse them, the reading
batch boundary can block on them, and the export can strip them:

- ``media_marker`` — a ``MEDIA:/path`` delivery marker (only valid in a chat reply).
- ``progress_footer`` — a cache/enrich/mark-read progress line (``Đã lưu cache/enrich
  và mark read: ...``).
- ``qa_note`` — a QA/checker/internal-validation note (``QA: ...``, ``Checker: ...``).
- ``export_status`` — an export status/freshness label or placeholder (``(untracked)``,
  ``Translation status unknown``, ``Missing asset: ...``).
- ``export_warning`` — a renderer/export warning or an export warning code
  (``asset_missing``, ``translation_stale``).
- ``absolute_asset_link`` — a Markdown/HTML image or link pointing at an absolute
  local path (``/Users/...``, ``file:``, ``C:\\``); artifacts link parsed assets
  relatively.

Rules apply line by line outside fenced code blocks, so a code listing quoting a log
line (``WARNING: ...``) is left alone. See ``docs/cli/artifacts.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Rules matched against each line (case-insensitive) outside fenced code blocks.
_LINE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (code, re.compile(pattern, re.IGNORECASE))
    for code, pattern in (
        ("media_marker", r"^\W*MEDIA:\s*\S"),
        (
            "progress_footer",
            r"đã\s+lưu\s+cache|\bda\s+luu\s+cache\b|\bcache\s*/\s*enrich\b|\bmark_read\b"
            r"|\bmark(?:ed)?\s+(?:as\s+)?read\s*:",
        ),
        (
            "qa_note",
            r"^[\s>*_#\-\[(]*(?:qa|qc|checker|internal[\s-]+validation|validation\s+notes?"
            r"|self[\s-]+check|(?:ghi\s+chú|kiểm\s+tra|note|notes)\s+qa)"
            r"(?:\s*/\s*checker)?(?:\s+(?:note|notes|check|checks|result|results|report))?"
            r"[\])*_]*\s*:"
            r"|^\W*\[(?:qa|qc|checker|internal[\s-]+validation)\]",
        ),
        (
            "export_status",
            r"translation\s+status\s+unknown|translation\s+may\s+be\s+outdated"
            r"|untranslated\s+[—-]+\s+original\s+text|not\s+translated\s+yet\s+[—-]"
            r"|missing\s+asset:",
        ),
        (
            # A status label alone on a line or closing a heading, as the export's TOC
            # and section notes print them.
            "export_status",
            r"^(?:\s*#{1,6}\s.*?|\W*)\((?:original|tracked|untracked|not\s+tracked"
            r"|may\s+be\s+outdated|skipped|fresh|stale)\)\W*$",
        ),
        (
            "export_warning",
            r"\b(?:asset_(?:missing|remote|unsupported)"
            r"|translation_(?:empty|stale|untracked|unreadable|missing_assets|contaminated))\b"
            r"|^\W*(?:renderer|export|weasyprint|playwright)\s+(?:warning|error)s?\s*:",
        ),
        (
            "absolute_asset_link",
            r"!?\[[^\]]*\]\(\s*<?(?:/|~/|file:|[a-z]:[\\/])"
            r"|<(?:img|a|source)\b[^>]*\b(?:src|href)\s*=\s*[\"']?(?:/|~/|file:|[a-z]:[\\/])",
        ),
    )
)

# Codes whose whole line is diagnostics, safe to drop from a rendered export. An
# absolute asset link is not: the image it points at may be real content.
OPERATIONAL_CODES: frozenset[str] = frozenset(
    {"media_marker", "progress_footer", "qa_note", "export_status", "export_warning"}
)

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_EXCERPT_CHARS = 120


@dataclass(frozen=True)
class HygieneFinding:
    """One line of an artifact that is diagnostics, not content (``line`` is 1-based)."""

    code: str
    line: int
    excerpt: str

    def describe(self) -> str:
        return f"line {self.line} ({self.code}): {self.excerpt}"


class ArtifactHygieneError(ValueError):
    """An artifact write carried diagnostics; nothing was written."""

    def __init__(self, what: str, findings: list[HygieneFinding]) -> None:
        self.findings = findings
        super().__init__(
            f"{what} contains operational text that does not belong in a reusable "
            f"artifact (keep it in the chat reply, job log, or report): "
            + summarize_findings(findings)
        )


def summarize_findings(findings: list[HygieneFinding], limit: int = 3) -> str:
    """A short, human-readable list of the first ``limit`` findings."""

    shown = "; ".join(finding.describe() for finding in findings[:limit])
    more = len(findings) - limit
    return shown + (f"; and {more} more" if more > 0 else "")


def scan_artifact_text(text: str) -> list[HygieneFinding]:
    """Every line of ``text`` that matches a hygiene rule (first matching rule wins)."""

    findings: list[HygieneFinding] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        opener = _FENCE_RE.match(line)
        if fence is not None:
            if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence):
                fence = None
            continue
        if opener:
            fence = opener.group(1)
            continue
        for code, pattern in _LINE_RULES:
            if pattern.search(line):
                excerpt = " ".join(line.split())
                if len(excerpt) > _EXCERPT_CHARS:
                    excerpt = excerpt[: _EXCERPT_CHARS - 1] + "…"
                findings.append(HygieneFinding(code=code, line=number, excerpt=excerpt))
                break
    return findings


def strip_operational_lines(text: str) -> tuple[str, list[HygieneFinding]]:
    """``text`` without its operational lines, plus the findings that were dropped.

    Only :data:`OPERATIONAL_CODES` lines are removed; other findings (an absolute asset
    link) are left for the caller to report.
    """

    dropped = [f for f in scan_artifact_text(text) if f.code in OPERATIONAL_CODES]
    if not dropped:
        return text, []
    lines = set(f.line for f in dropped)
    kept = [line for number, line in enumerate(text.splitlines(), start=1) if number not in lines]
    return "\n".join(kept) + ("\n" if text.endswith("\n") else ""), dropped


def ensure_clean_artifact(text: str, what: str) -> None:
    """Raise :class:`ArtifactHygieneError` when ``text`` carries any finding."""

    findings = scan_artifact_text(text)
    if findings:
        raise ArtifactHygieneError(what, findings)
