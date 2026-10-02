"""Keep operational chatter out of reusable reading artifacts.

Reading/translation jobs produce two kinds of text: the **artifact** (a translated
section, an annotation summary — book content, reused by later jobs and exported to
readers) and **diagnostics** (delivery markers, progress footers, QA results,
export status labels). The root fix is giving diagnostics their own channel: the
``notes`` field of ``write_section_translation`` for QA/terminology remarks, the chat
reply for ``MEDIA:`` and progress lines, the report JSON for export status — and the
``bookgraph-reader`` skill tells agents to use them. This module is only the safety
net behind that: a deliberately small set of rules for text that is never book
content, so writers can refuse it, the reading batch boundary can block on it, and
the export can strip it. Free-form QA prose is not guessed at — a rule loose enough
to catch it also refuses real book text.

- ``media_marker`` — a ``MEDIA:/path`` delivery marker (only valid in a chat reply);
  upper-case ``MEDIA:`` followed by a local path, so ``Media: print and radio`` is prose.
- ``progress_footer`` — a cache/enrich/mark-read progress line (``Đã lưu cache/enrich
  và mark read: ...``).
- ``export_status`` — an export status/freshness label or placeholder: ``(untracked)``
  alone on its line (a heading such as ``The Iliad (original)`` is prose),
  ``Translation status unknown``, ``Missing asset: ...``.
- ``export_warning`` — a renderer/export warning or an export warning code
  (``asset_missing``, ``translation_stale``).
- ``absolute_asset_link`` — an image (``![…](…)``, ``<img|source src=…>``, or a
  reference definition of an image file) pointing at an absolute local path
  (``/Users/...``, ``file:``, ``C:\\``); artifacts link parsed assets relatively.
  Ordinary links (``[docs](/docs/intro)``) and protocol-relative URLs are not assets.

Rules apply line by line to prose only: fenced and indented code blocks and inline
code spans are skipped, so a code listing quoting a log line (``WARNING: ...``) or a
mention of `` `mark_read` `` is left alone. Stored annotation text is collapsed to one
line, so :func:`scan_artifact_text` also has a ``collapsed`` mode in which the
line-start rules match after any whitespace. See ``docs/cli/artifacts.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ``{s}`` marks where a label must start: the start of the line (after punctuation or
# emoji such as ``**`` / ``✅``), or — in collapsed mode — after any whitespace.
_LINE_START = r"^\W*"
_COLLAPSED_START = r"(?:^|(?<=\s))\W*"
_ABSOLUTE = r"(?:/(?!/)|~/|file:|[a-z]:[\\/])"

# (code, pattern, case_sensitive, line_only). ``line_only`` rules only make sense on a
# real line (a label alone on it, a reference definition) and are skipped in
# collapsed mode.
_RULES: tuple[tuple[str, str, bool, bool], ...] = (
    ("media_marker", r"{s}MEDIA:\s*" + _ABSOLUTE.replace("[a-z]", "[A-Za-z]"), True, False),
    (
        "progress_footer",
        r"đã\s+lưu\s+cache|\bda\s+luu\s+cache\b|\bcache\s*/\s*enrich\b|\bmark_read\b"
        r"|\bmark(?:ed)?\s+(?:as\s+)?read\s*:",
        False,
        False,
    ),
    (
        "export_status",
        r"translation\s+status\s+unknown|translation\s+may\s+be\s+outdated"
        r"|untranslated\s+[—-]+\s+original\s+text|not\s+translated\s+yet\s+[—-]"
        r"|missing\s+asset:",
        False,
        False,
    ),
    (
        # A status label alone on its line, as the export's TOC and notes print it.
        "export_status",
        r"^\W*\((?:original|tracked|untracked|not\s+tracked|may\s+be\s+outdated|skipped"
        r"|fresh|stale)\)\W*$",
        False,
        True,
    ),
    (
        "export_warning",
        r"\b(?:asset_(?:missing|remote|unsupported)"
        r"|translation_(?:empty|stale|untracked|unreadable|missing_assets|contaminated))\b"
        r"|{s}(?:renderer|export|weasyprint|playwright)\s+(?:warning|error)s?\s*:",
        False,
        False,
    ),
    (
        "absolute_asset_link",
        r"!\[[^\]]*\]\(\s*<?"
        + _ABSOLUTE
        + r"|<(?:img|source)\b[^>]*\bsrc\s*=\s*[\"']?"
        + _ABSOLUTE,
        False,
        False,
    ),
    (
        "absolute_asset_link",
        r"^\s{0,3}\[[^\]]+\]:\s*<?" + _ABSOLUTE + r"\S*\.(?:png|jpe?g|gif|svg|webp)\b",
        False,
        True,
    ),
)


def _compile(collapsed: bool) -> tuple[tuple[str, re.Pattern[str]], ...]:
    start = _COLLAPSED_START if collapsed else _LINE_START
    return tuple(
        (code, re.compile(pattern.replace("{s}", start), 0 if exact else re.IGNORECASE))
        for code, pattern, exact, line_only in _RULES
        if not (collapsed and line_only)
    )


_LINE_RULES = _compile(collapsed=False)
_COLLAPSED_RULES = _compile(collapsed=True)

# Codes whose whole line is diagnostics, safe to drop from a rendered export. An
# absolute asset link is not: the image it points at may be real content.
OPERATIONAL_CODES: frozenset[str] = frozenset(
    {"media_marker", "progress_footer", "export_status", "export_warning"}
)

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INDENTED_CODE_RE = re.compile(r"^(?: {4}|\t)")
_CODE_SPAN_RE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")
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


def scan_artifact_text(text: str, *, collapsed: bool = False) -> list[HygieneFinding]:
    """Every prose line of ``text`` that matches a hygiene rule (first matching rule wins).

    ``collapsed`` is for text stored as one line (annotation summaries and glosses):
    line-start rules then also match after any whitespace, and rules that need a real
    line are skipped.
    """

    rules = _COLLAPSED_RULES if collapsed else _LINE_RULES
    findings: list[HygieneFinding] = []
    fence: str | None = None
    # An indented code block starts after a blank line (it cannot interrupt a paragraph)
    # and runs while lines stay indented or blank.
    previous_blank = True
    in_indented_code = False
    for number, line in enumerate(text.splitlines(), start=1):
        opener = _FENCE_RE.match(line)
        if fence is not None:
            # A closing fence is fence characters only (no info string), of the same
            # kind and at least as long as the opener (CommonMark).
            closer = line.strip()
            if (
                opener
                and closer == opener.group(1)
                and closer[0] == fence[0]
                and len(closer) >= len(fence)
            ):
                fence = None
            continue
        if opener:
            fence = opener.group(1)
            continue
        blank = not line.strip()
        if _INDENTED_CODE_RE.match(line) and not blank and (previous_blank or in_indented_code):
            in_indented_code = True
            previous_blank = False
            continue
        if not blank:
            in_indented_code = False
        previous_blank = blank
        prose = _CODE_SPAN_RE.sub(" ", line)
        for code, pattern in rules:
            if pattern.search(prose):
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
