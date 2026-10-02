"""Check that a translation kept its source section's structural Markdown.

A translation should change the words a reader sees, not what the book's links point
at. Link labels, image alt text, and prose may be translated; these must survive
byte-for-byte, because intra-book references, TOC anchors, and exports depend on them:

- ``link`` — Markdown link destinations (``[label](ch03.html#sec_x)``, ``(#fig_y)``,
  autolinks, links resolved from reference definitions) and HTML ``href`` values;
- ``image`` — Markdown image destinations and HTML ``src`` values (file paths);
- ``reference`` — reference-style definitions, identifier and destination
  (``[graphql-spec]: https://…``);
- ``html_id`` — HTML ``id`` / ``name`` anchors;
- ``heading_id`` — explicit heading ids (``## Title {#sec_x}``).

:func:`check_translation_structure` compares the multiset of targets in the source and
the translation, so a dropped, added, or rewritten target is reported. The one allowed
rewrite is a broken local image path normalised to one that resolves, and a translation
may add local images that resolve (it carries the section's figures, which are not part
of the section text); both are decided by the caller's ``asset_resolves``. Code
spans/blocks and HTML comments are not structure.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token

from bookgraph.models import Section, StructuralTargetKind, TranslationStructureIssue

Target = tuple[StructuralTargetKind, str]

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# A start tag, quote-aware: a '>' inside a quoted attribute value does not end it.
_HTML_TAG_RE = re.compile(r"""<[A-Za-z][\w:-]*(?P<attrs>(?:"[^"]*"|'[^']*'|[^'">])*)>""")
# One attribute, walked left to right so a quoted value (``alt='a src=x'``) is consumed
# whole and never read as attributes of its own.
_HTML_ATTR_RE = re.compile(
    r"""(?P<name>[^\s"'<>/=]+)(?:\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s"'=<>`]+)))?"""
)
_HTML_ATTR_KINDS: dict[str, StructuralTargetKind] = {
    "id": "html_id",
    "name": "html_id",
    "href": "link",
    "src": "image",
}
_HEADING_ID_RE = re.compile(r"\{\s*#(?P<id>[^\s{}]+)[^{}]*\}\s*$")
_REFERENCE_LABEL_RE = re.compile(r"^ {0,3}\[(?P<label>(?:\\.|[^\]\\])+)\]:")
_REMOTE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    # Keep destinations as written: the default normaliser percent-encodes them, which
    # would hide a translator "fixing" ``%20`` into a space (or the reverse).
    md.normalizeLink = lambda url: url  # type: ignore[method-assign]
    return md


_MD = _parser()


def section_source_markdown(section: Section) -> str:
    """The Markdown a translator works from: the section's title heading and its text."""

    return f"# {section.title}\n\n{section.text}"


def structural_targets(markdown: str) -> Counter[Target]:
    """Every structural target in ``markdown``, counted, in order of appearance."""

    env: dict[str, object] = {}
    targets: Counter[Target] = Counter()
    tokens = _MD.parse(markdown, env)
    for index, token in enumerate(tokens):
        if token.type == "html_block":
            targets.update(_html_targets(token.content))
        elif token.type == "inline":
            if index > 0 and tokens[index - 1].type == "heading_open":
                match = _HEADING_ID_RE.search(token.content)
                if match:
                    targets[("heading_id", match.group("id"))] += 1
            targets.update(_inline_targets(token.children or []))
    targets.update(_reference_targets(markdown, env))
    return targets


def _inline_targets(children: list[Token]) -> Iterable[Target]:
    for child in children:
        if child.type == "link_open":
            yield "link", str(child.attrGet("href") or "")
        elif child.type == "image":
            yield "image", str(child.attrGet("src") or "")
        elif child.type == "html_inline":
            yield from _html_targets(child.content)


def _html_targets(html: str) -> Iterable[Target]:
    for tag in _HTML_TAG_RE.finditer(_HTML_COMMENT_RE.sub("", html)):
        for match in _HTML_ATTR_RE.finditer(tag.group("attrs")):
            kind = _HTML_ATTR_KINDS.get(match.group("name").lower())
            value = next((v for v in match.group("dq", "sq", "bare") if v is not None), None)
            if kind is not None and value is not None:
                yield kind, value


def _reference_targets(markdown: str, env: dict[str, object]) -> Iterable[Target]:
    references = env.get("references")
    if not isinstance(references, dict):
        return
    lines = markdown.splitlines()
    for normalized, definition in references.items():
        label = normalized
        line_map = definition.get("map")
        if line_map and line_map[0] < len(lines):
            match = _REFERENCE_LABEL_RE.match(lines[line_map[0]])
            if match:
                label = match.group("label")
        yield "reference", f"[{label}]: {definition.get('href', '')}"


def check_translation_structure(
    source: str,
    translated: str,
    *,
    asset_resolves: Callable[[str], bool] | None = None,
) -> list[TranslationStructureIssue]:
    """The structural targets ``translated`` dropped or added relative to ``source``.

    An empty list means every link destination, image path, reference definition, HTML
    anchor, and heading id survived unchanged. ``asset_resolves`` (optional) says
    whether a local image path points at a real file; with it, added images that
    resolve are allowed, and a source image path that does not resolve may be replaced
    by one that does — normalising a known broken asset path is the one permitted
    rewrite. Missing targets are listed first, in source order, then added ones in
    translation order.
    """

    expected = structural_targets(source)
    actual = structural_targets(translated)
    missing = expected - actual
    added = actual - expected
    if asset_resolves is not None:
        missing, added = _forgive_fixed_images(missing, added, asset_resolves)
    issues = [
        TranslationStructureIssue(kind=kind, target=target, change="missing", count=count)
        for (kind, target), count in missing.items()
    ]
    issues.extend(
        TranslationStructureIssue(kind=kind, target=target, change="added", count=count)
        for (kind, target), count in added.items()
    )
    return issues


def _forgive_fixed_images(
    missing: Counter[Target], added: Counter[Target], asset_resolves: Callable[[str], bool]
) -> tuple[Counter[Target], Counter[Target]]:
    """Drop image changes that make the translation's images work, not break.

    A local image the translation added that resolves is a figure carried over from the
    section's assets (which are not in its text) or a fixed path; a broken local source
    image is forgiven when such a resolving replacement exists for it.
    """

    fixed = [
        target
        for target in added
        if target[0] == "image" and _is_local(target[1]) and asset_resolves(target[1])
        for _ in range(added[target])
    ]
    broken = [
        target
        for target in missing
        if target[0] == "image" and _is_local(target[1]) and not asset_resolves(target[1])
        for _ in range(missing[target])
    ]
    return missing - Counter(broken[: len(fixed)]), added - Counter(fixed)


def _is_local(target: str) -> bool:
    return bool(target) and not _REMOTE_RE.match(target)


def local_asset_resolver(root: Path, bases: list[Path]) -> Callable[[str], bool]:
    """An ``asset_resolves`` that finds an image path under ``bases``, inside ``root``.

    Mirrors how ``bookgraph export translated-pdf`` resolves image links: the query and
    fragment are dropped, the path is unquoted, and only regular files inside the
    workspace count.
    """

    def resolves(target: str) -> bool:
        raw = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not raw:
            return False
        try:
            root_real = root.resolve()
        except (OSError, ValueError):
            return False
        candidate = Path(raw)
        options = [candidate] if candidate.is_absolute() else [base / candidate for base in bases]
        for option in options:
            try:
                real = option.resolve()
                if real.is_relative_to(root_real) and real.is_file():
                    return True
            except (OSError, ValueError):
                continue
        return False

    return resolves


def describe_structure_issues(issues: list[TranslationStructureIssue], limit: int = 5) -> str:
    """A one-line summary of ``issues`` for warnings and batch messages."""

    parts = [
        f"{issue.kind} '{issue.target}' {issue.change}"
        + (f" ×{issue.count}" if issue.count > 1 else "")
        for issue in issues[:limit]
    ]
    if len(issues) > limit:
        parts.append(f"and {len(issues) - limit} more")
    return "; ".join(parts)
