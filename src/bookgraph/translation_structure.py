"""Check that a translation kept its source section's structural Markdown.

A translation should change the words a reader sees, not what the book's links point
at. Link labels, image alt text, and prose may be translated; these must survive
byte-for-byte, because intra-book references, TOC anchors, and exports depend on them:

- ``link`` — Markdown link destinations (``[label](ch03.html#sec_x)``, ``(#fig_y)``,
  autolinks, links resolved from reference definitions), HTML ``href`` values, and
  ``src`` on elements other than ``<img>`` (``<script>``, ``<iframe>``, …);
- ``image`` — Markdown image destinations and ``<img src>`` values (file paths);
- ``reference`` — reference-style definitions, identifier (compared as CommonMark
  normalises it: case- and whitespace-insensitive) and destination
  (``[graphql-spec]: https://…``);
- ``html_id`` — HTML ``id`` / ``name`` anchors;
- ``heading_id`` — explicit heading ids (``## Title {#sec_x}``).

:func:`check_translation_structure` compares the multiset of targets in the source and
the translation, so a dropped, added, or rewritten target is reported. The one allowed
rewrite is a broken local image path normalised to one that resolves, and a translation
may add local images that resolve (it carries the section's figures, which are not part
of the section text); both are decided by the caller's ``asset_resolves``. ``data:``
images are embedded as they are, so they always count as resolving. Code spans/blocks
and HTML comments are not structure.

The source side is :func:`section_source_markdown`: parsers store code blocks as plain
text with their fences removed, so it re-fences them from the section's canonical
blocks; code a translator left unfenced is forgiven too (see
:func:`check_translation_structure`). Parsers also drop reference definitions from the
section text (``[spec]: …`` never reaches ``Section.text``), so the ``reference`` kind
only protects definitions a section's text actually carries (sections written by hand
or by a future parser that keeps them).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

from bookgraph.assets import resolve_workspace_link
from bookgraph.models import (
    CanonicalBlock,
    Section,
    StructuralTargetKind,
    TranslationStructureIssue,
)

Target = tuple[StructuralTargetKind, str]

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# A start tag, quote-aware: a '>' inside a quoted attribute value does not end it.
_HTML_TAG_RE = re.compile(r"""<(?P<tag>[A-Za-z][\w:-]*)(?P<attrs>(?:"[^"]*"|'[^']*'|[^'">])*)>""")
# One attribute, walked left to right so a quoted value (``alt='a src=x'``) is consumed
# whole and never read as attributes of its own.
_HTML_ATTR_RE = re.compile(
    r"""(?P<name>[^\s"'<>/=]+)(?:\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s"'=<>`]+)))?"""
)
_HTML_ATTR_KINDS: dict[str, StructuralTargetKind] = {
    "id": "html_id",
    "name": "html_id",
    "href": "link",
    "src": "link",  # ``image`` on ``<img>`` only, as the export only embeds those
}
_HEADING_ID_RE = re.compile(r"\{\s*#(?P<id>[^\s{}]+)[^{}]*\}\s*$")
_REMOTE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_DATA_IMAGE_RE = re.compile(r"^data:image/", re.IGNORECASE)
# An absolute local path (``/x``, ``~/x``, ``file:``, ``C:\x``). The reading-agent
# contract links each figure/table by its relative ``AssetRef.link``, never by its
# absolute path, so an absolute image link is never a carried figure or a fix.
_ABSOLUTE_RE = re.compile(r"^(?:/(?!/)|~/|file:|[A-Za-z]:[\\/])")
_BACKTICK_RUN_RE = re.compile(r"`+")
_TARGET_DISPLAY_LIMIT = 80


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    # Keep destinations as written: the default normaliser percent-encodes them, which
    # would hide a translator "fixing" ``%20`` into a space (or the reverse).
    md.normalizeLink = lambda url: url  # type: ignore[method-assign]
    return md


_MD = _parser()


def section_source_markdown(
    section: Section, blocks: Mapping[str, CanonicalBlock] | None = None
) -> str:
    """The Markdown a translator works from: the section's title heading and its text.

    With the parsed document's ``blocks``, the text is rebuilt from the section's blocks
    with code blocks (``metadata.code``) and equations fenced: parsers store them as
    plain text with the fences removed, and parsing ``handlers[0](event)`` or
    ``<div id="app">`` sample code as Markdown would invent links and anchors. The
    rebuild is used only when, fences aside, it reproduces ``Section.text`` exactly —
    joined with or without title blocks, since segmenters differ on that (the heading
    and bookmark segmenters drop them, the token/page one keeps them). Otherwise (no
    blocks, missing block ids, a segmenter that joins differently) ``Section.text`` is
    used as is.
    """

    owned = [blocks[b] for b in section.block_ids if b in blocks] if blocks else []
    rebuilt = _rebuilt_text(section, owned) if owned else None
    return f"# {section.title}\n\n{section.text if rebuilt is None else rebuilt}"


def _rebuilt_text(section: Section, owned: list[CanonicalBlock]) -> str | None:
    for keep_titles in (False, True):
        kept = [
            block
            for block in owned
            if block.text.strip() and (keep_titles or block.type != "title")
        ]
        if "\n\n".join(block.text.strip() for block in kept) != section.text:
            continue
        return "\n\n".join(
            _fenced(block.text.strip())
            if block.metadata.get("code") or block.type == "equation"
            else block.text.strip()
            for block in kept
        )
    return None


def _fenced(code: str) -> str:
    longest = max((len(run) for run in _BACKTICK_RUN_RE.findall(code)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{code}\n{fence}"


def check_section_translation(
    section: Section,
    body: str,
    *,
    blocks: Mapping[str, CanonicalBlock] | None = None,
    asset_resolves: Callable[[str], bool] | None = None,
) -> list[TranslationStructureIssue]:
    """Check a translation ``body`` (frontmatter already split off) against ``section``.

    The one entry point the MCP views, reading-batch completion, and the translated
    export share, so they always judge the same text the same way.
    """

    return check_translation_structure(
        section_source_markdown(section, blocks), body, asset_resolves=asset_resolves
    )


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
    targets.update(_reference_targets(env))
    return targets


def _code_contents(markdown: str) -> list[str]:
    """The text of every code block and code span in ``markdown``."""

    contents: list[str] = []
    for token in _MD.parse(markdown):
        if token.type in {"fence", "code_block"}:
            contents.append(token.content)
        for child in token.children or []:
            if child.type == "code_inline":
                contents.append(child.content)
    return contents


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
        is_img = tag.group("tag").lower() == "img"
        for match in _HTML_ATTR_RE.finditer(tag.group("attrs")):
            name = match.group("name").lower()
            kind = "image" if is_img and name == "src" else _HTML_ATTR_KINDS.get(name)
            value = next((v for v in match.group("dq", "sq", "bare") if v is not None), None)
            if kind is not None and value is not None:
                yield kind, value


def _reference_targets(env: dict[str, object]) -> Iterable[Target]:
    # Keyed by the CommonMark-normalised label (case-folded, whitespace collapsed), so
    # ``[Spec]`` → ``[spec]`` — which still matches every use — is not a change,
    # wherever the definition sits. markdown-it upper-cases the key; show it lower.
    references = env.get("references")
    if not isinstance(references, dict):
        return
    for label, definition in references.items():
        yield "reference", f"[{label.lower()}]: {definition.get('href', '')}"


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
    rewrite. Added targets that the source's code blocks and spans yield when parsed as
    Markdown are not reported (up to their count): that is sample code the translation
    left unfenced. Missing
    targets are listed first, in source order, then added ones in translation order.
    """

    expected = structural_targets(source)
    actual = structural_targets(translated)
    missing = expected - actual
    added = actual - expected
    # Targets the source's code would yield if read as Markdown: what a translation
    # that left the sample code unfenced adds, and nothing else.
    code_targets: Counter[Target] = Counter()
    for code in _code_contents(source):
        code_targets.update(structural_targets(code))
    added -= code_targets
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
        if target[0] == "image" and _embeddable(target[1], asset_resolves)
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


def _embeddable(target: str, asset_resolves: Callable[[str], bool]) -> bool:
    """A ``data:image/…`` URI (embedded as is) or a local path that resolves."""

    if _DATA_IMAGE_RE.match(target):
        return True
    return _is_local(target) and asset_resolves(target)


def local_asset_resolver(root: Path, bases: list[Path]) -> Callable[[str], bool]:
    """An ``asset_resolves`` that resolves relative image links as the export does.

    Both use :func:`bookgraph.assets.resolve_workspace_link`: the query and fragment
    are dropped, the path is unquoted, and only regular files inside ``root`` count.
    An absolute path never resolves here, even to a workspace file: the reading-agent
    contract links carried figures by their relative ``AssetRef.link``, never by their
    absolute ``AssetRef.path``, so adding one is a structure change, not a carried
    figure or a fixed path.
    """

    def resolves(target: str) -> bool:
        if _ABSOLUTE_RE.match(target):
            return False
        return resolve_workspace_link(root, target, bases) is not None

    return resolves


def describe_structure_issues(issues: list[TranslationStructureIssue], limit: int = 5) -> str:
    """A one-line summary of ``issues`` for warnings and batch messages."""

    parts = [
        f"{issue.kind} '{_shorten(issue.target)}' {issue.change}"
        + (f" ×{issue.count}" if issue.count > 1 else "")
        for issue in issues[:limit]
    ]
    if len(issues) > limit:
        parts.append(f"and {len(issues) - limit} more")
    return "; ".join(parts)


def _shorten(target: str) -> str:
    # A ``data:`` URI can be megabytes of base64; messages only need to identify it.
    # Elide the middle so a long path keeps both its root and its file name.
    if len(target) <= _TARGET_DISPLAY_LIMIT:
        return target
    head = (_TARGET_DISPLAY_LIMIT - 1) // 2
    tail = _TARGET_DISPLAY_LIMIT - 1 - head
    return target[:head] + "…" + target[-tail:]
