"""Embed a reading edition's images, or leave them out cleanly.

Shared by the translated export's assembler (:mod:`bookgraph.exports.translated`):
every image — a Markdown ``image`` token, a raw HTML ``<img>``, or a parsed asset
block — is embedded as a ``data:`` URI, so the page is self-contained. One that
cannot be embedded (missing, remote, outside the workspace, not an image) is left out
of the reader-facing output, keeping its caption and the surrounding prose, and is
reported as an :class:`~bookgraph.exports.models.ExportWarning` with the section, the
reference, and the file that carries it. With ``show_status`` it is replaced by a
visible *Missing asset* placeholder instead.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import dataclass, field
from html import escape, unescape
from pathlib import Path

from markdown_it.token import Token

from bookgraph.assets import resolve_workspace_link
from bookgraph.exports.html_attrs import HTML_ATTR, HTML_ATTR_RE
from bookgraph.exports.models import (
    ASSET_MISSING,
    ASSET_REMOTE,
    ASSET_UNSUPPORTED,
    ExportWarning,
)
from bookgraph.utils import is_url
from bookgraph.workspace import WorkspacePaths

# Image types every supported renderer can draw from a data: URI.
_EMBEDDABLE_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/svg+xml", "image/webp"}
)

# Raw HTML scanning for ``<img>`` tags, attribute by attribute (see ``html_attrs``).
# ``<img`` followed by whitespace, ``/`` or ``>``: not ``\b``, which would also match
# custom elements such as ``<img-zoom>`` (``\b`` falls between ``g`` and ``-``).
_IMG_OPEN = r"<img(?=[\s/>])"
# Alternatives, in order: an HTML comment (left untouched, so a commented-out image is
# neither embedded nor reported), a well-formed ``<img>`` tag, and a malformed one
# (reported, so it cannot vanish silently under the CSP).
_HTML_IMG_SCAN_RE = re.compile(
    rf"(?P<comment><!--.*?-->)|(?P<img>{_IMG_OPEN}(?:\s+{HTML_ATTR})*\s*/?>)|(?P<bad>{_IMG_OPEN}[^>]*>)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class AssetCounter:
    embedded: int = 0
    missing: int = 0


@dataclass(frozen=True)
class AssetOrigin:
    """Where an asset reference was read from, for its warning: a workspace-relative
    file (translation artifact, ``document.json`` or ``sections.jsonl``) and, for a
    reference inside a parsed block, that block's id."""

    source_path: str | None
    block_id: str | None = None


@dataclass(kw_only=True)
class ImageEmbedder:
    """Image embedding and warning collection for one export run.

    A base class: the export assembler adds the section rendering on top and shares
    ``warnings`` with it.
    """

    workspace: WorkspacePaths
    parsed_dir: Path
    show_status: bool = False
    warnings: list[ExportWarning] = field(default_factory=list)
    _data_uris: dict[Path, str | None] = field(default_factory=dict)

    def _parsed_bases(self) -> list[Path]:
        return [self.parsed_dir / "images", self.parsed_dir, self.workspace.root]

    def _rewrite_images(
        self,
        tokens: list[Token],
        section_id: str,
        bases: list[Path],
        counter: AssetCounter,
        source: str | None,
        block_id: str | None = None,
    ) -> None:
        """Embed every image as a data: URI, or drop it (a placeholder when debugging).

        Covers Markdown ``image`` tokens and raw HTML ``<img>`` tags (artifacts may
        carry HTML, e.g. MinerU tables): the page's CSP only allows ``data:`` images,
        so an ``<img>`` left untouched would vanish silently instead of being embedded
        or reported. A paragraph left with nothing but the dropped image is hidden, so
        the reader sees the caption that follows it and no empty gap.
        """

        origin = AssetOrigin(source, block_id)
        for position, token in enumerate(tokens):
            if token.type == "html_block":
                missing_before = counter.missing
                token.content = self._rewrite_html_images(
                    token.content, section_id, bases, counter, origin
                )
                if counter.missing > missing_before and not self.show_status:
                    token.content = _strip_emptied_html(token.content)
            if not token.children:
                continue
            children: list[Token] = []
            dropped = False
            for child in token.children:
                if child.type == "html_inline":
                    original = child.content
                    child.content = self._rewrite_html_images(
                        original, section_id, bases, counter, origin
                    )
                    if original.strip() and not child.content.strip():
                        dropped = True  # the tag was only a dropped image
                        continue
                elif child.type == "image":
                    src = str(child.attrGet("src") or "")
                    uri = self._link_data_uri(src, section_id, bases, origin)
                    if uri is None:
                        counter.missing += 1
                        placeholder = self._missing(src)
                        if not placeholder:
                            dropped = True
                            continue
                        child = _html_inline(placeholder)
                    else:
                        counter.embedded += 1
                        child.attrSet("src", uri)
                children.append(child)
            if dropped:
                children = _drop_empty_wrappers(children)
            token.children = children
            if dropped and not any(_has_content(child) for child in children):
                _hide_paragraph(tokens, position)

    def _rewrite_html_images(
        self,
        html: str,
        section_id: str,
        bases: list[Path],
        counter: AssetCounter,
        origin: AssetOrigin,
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            if match.group("comment"):
                return match.group(0)
            tag = match.group(0)
            src_attr = _html_src_attr(tag[4:]) if match.group("img") else None
            if src_attr is None:
                self._warn(
                    ASSET_MISSING, "HTML <img> tag has no usable src", section_id, tag, origin
                )
                counter.missing += 1
                return self._missing(tag)
            src, (start, end) = src_attr
            uri = self._link_data_uri(src, section_id, bases, origin)
            if uri is None:
                counter.missing += 1
                return self._missing(src)
            counter.embedded += 1
            start, end = start + 4, end + 4  # offsets are relative to the text after "<img"
            return f'{tag[:start]}src="{escape(uri, quote=True)}"{tag[end:]}'

        return _HTML_IMG_SCAN_RE.sub(replace, html)

    def _link_data_uri(
        self, src: str, section_id: str, bases: list[Path], origin: AssetOrigin
    ) -> str | None:
        if not src:
            self._warn(ASSET_MISSING, "image link has an empty target", section_id, src, origin)
            return None
        if src.startswith("data:"):
            return src
        if is_url(src):
            self._warn(
                ASSET_REMOTE,
                f"remote image '{src}' is not fetched; exports embed workspace files only",
                section_id,
                src,
                origin,
            )
            return None
        path = self._resolve_link(src, bases)
        if path is None:
            self._warn(
                ASSET_MISSING,
                f"image '{src}' was not found inside the workspace",
                section_id,
                src,
                origin,
            )
            return None
        return self._data_uri(path, section_id, src, origin)

    def _resolve_link(self, link: str, bases: list[Path]) -> Path | None:
        """Resolve an image link to a regular file that stays inside the workspace."""

        return resolve_workspace_link(self.workspace.root, link, bases)

    def _data_uri(
        self, path: Path, section_id: str, reference: str, origin: AssetOrigin
    ) -> str | None:
        key = path.resolve()
        if key not in self._data_uris:
            mime, _ = mimetypes.guess_type(key.name)
            if mime not in _EMBEDDABLE_MIME_TYPES:
                self._data_uris[key] = None
            else:
                try:
                    payload = base64.b64encode(key.read_bytes()).decode("ascii")
                    self._data_uris[key] = f"data:{mime};base64,{payload}"
                except OSError:
                    self._data_uris[key] = None
        uri = self._data_uris[key]
        if uri is None:
            self._warn(
                ASSET_UNSUPPORTED,
                f"asset '{reference}' is not an embeddable image (png, jpeg, gif, svg, webp)",
                section_id,
                reference,
                origin,
            )
        return uri

    def _missing(self, reference: str) -> str:
        """Where an asset could not be embedded: a visible placeholder only in debug mode."""

        if not self.show_status:
            return ""
        return f'<span class="missing-asset">Missing asset: {escape(reference)}</span>'

    def _warn(
        self,
        code: str,
        message: str,
        section_id: str,
        reference: str | None = None,
        origin: AssetOrigin | None = None,
    ) -> None:
        self.warnings.append(
            ExportWarning(
                code=code,
                message=message,
                section_id=section_id,
                reference=reference,
                source_path=origin.source_path if origin else None,
                block_id=origin.block_id if origin else None,
            )
        )


def _html_src_attr(attributes: str) -> tuple[str, tuple[int, int]] | None:
    """The unescaped ``src`` value of an ``<img>`` tag's attributes, with its span.

    ``attributes`` is the tag text after ``<img``. Attributes are walked one at a
    time, so only a real ``src`` attribute counts (not ``data-src``, nor ``src=``
    inside another attribute's quoted value).
    """

    for attr in HTML_ATTR_RE.finditer(attributes):
        if attr.group(1).lower() != "src":
            continue
        raw = attr.group(2)
        if raw is None:
            return None
        if raw[:1] in {'"', "'"}:
            raw = raw[1:-1]
        return unescape(raw), attr.span()
    return None


# Inline/block HTML elements that render nothing once their content is gone. Table
# parts are left out on purpose: an empty cell keeps a table's shape.
_WRAPPER_TAGS = "a|p|figure|picture|span|em|strong|b|i|s|u|div|center|small|sup|sub"
_EMPTY_ELEMENT_RE = re.compile(rf"<({_WRAPPER_TAGS})\b([^>]*)>\s*</\1\s*>", re.IGNORECASE)
_OPEN_TAG_RE = re.compile(r"^\s*<([a-z][\w-]*)\b([^>]*)(?<!/)>\s*$", re.IGNORECASE)
_CLOSE_TAG_RE = re.compile(r"^\s*</([a-z][\w-]*)\s*>\s*$", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]*>")
# An element with an ``id``/``name`` is a link target (EPUB ``<a id="fig-1"></a>``):
# content even when it shows no text, so it is never stripped as an empty wrapper.
_TARGET_ATTR_RE = re.compile(r"""(?:^|\s)(?:id|name)\s*=""", re.IGNORECASE)
_TARGET_TAG_RE = re.compile(r"""<[a-z][\w-]*\s[^>]*?(?<=\s)(?:id|name)\s*=""", re.IGNORECASE)
# Elements that render something without any text inside them.
_SELF_RENDERING_RE = re.compile(
    r"<(?:img|hr|svg|video|audio|iframe|object|embed|canvas|math)\b", re.IGNORECASE
)


def _html_visible(html: str) -> bool:
    """Whether a piece of raw HTML is content: it renders something (an image, a rule,
    an SVG, media, text outside tags) or carries a link target (``id``/``name``)."""

    return bool(
        _SELF_RENDERING_RE.search(html)
        or _TARGET_TAG_RE.search(html)
        or _ANY_TAG_RE.sub("", html).strip()
    )


def _has_content(token: Token) -> bool:
    """Whether an inline child renders anything a reader would see.

    Wrapper tags alone (Markdown ``*_open``/``*_close`` such as links and emphasis, or
    a raw HTML ``<a …>``/``</a>``) render nothing visible; text and raw HTML count
    only when they show something.
    """

    if token.type in {"softbreak", "hardbreak"} or token.nesting != 0:
        return False
    if token.type == "text":
        return bool(token.content.strip())
    if token.type == "html_inline":
        return _html_visible(token.content)
    return True


def _wrapper_key(token: Token) -> tuple[str, int] | None:
    """``(kind, +1 open / -1 close)`` for an inline wrapper token, else ``None``."""

    if token.nesting != 0:
        return token.type.rsplit("_", 1)[0], token.nesting
    if token.type == "html_inline":
        if match := _OPEN_TAG_RE.match(token.content):
            if _TARGET_ATTR_RE.search(match.group(2)):
                return None  # a link target: content, not a wrapper
            return "html:" + match.group(1).lower(), 1
        if match := _CLOSE_TAG_RE.match(token.content):
            return "html:" + match.group(1).lower(), -1
    return None


def _drop_empty_wrappers(children: list[Token]) -> list[Token]:
    """Remove wrappers left around nothing once an image was dropped.

    Covers Markdown links and emphasis (``[![fig](gone.png)](url)``,
    ``*![fig](gone.png)*``) and raw HTML pairs (``<a href="…"><img src="gone.png"></a>``),
    so neither an empty, clickable ``<a>`` nor an empty ``<em>`` stands in for the
    figure. Called only after an image was dropped.
    """

    empty: set[int] = set()
    opened: list[tuple[str, int]] = []
    for index, child in enumerate(children):
        key = _wrapper_key(child)
        if key is None:
            continue
        kind, direction = key
        if direction > 0:
            opened.append((kind, index))
            continue
        while opened and opened[-1][0] != kind:
            opened.pop()  # an unbalanced raw tag: never pair across it
        if not opened:
            continue
        _, start = opened.pop()
        inner = [c for i, c in enumerate(children[start + 1 : index], start + 1) if i not in empty]
        if not any(_has_content(c) for c in inner):
            empty.update(range(start, index + 1))
    return [child for index, child in enumerate(children) if index not in empty]


def _strip_unless_target(match: re.Match[str]) -> str:
    return match.group(0) if _TARGET_ATTR_RE.search(match.group(2)) else ""


def _strip_emptied_html(html: str) -> str:
    """Strip the wrapper elements an HTML block's dropped image left empty.

    Repeats until stable (``<p><a …></a></p>`` → ``<p></p>`` → nothing). A block with
    nothing visible left becomes empty, so it renders nothing at all.
    """

    while True:
        stripped = _EMPTY_ELEMENT_RE.sub(_strip_unless_target, html)
        if stripped == html:
            break
        html = stripped
    return html if _html_visible(html) else ""


def _hide_paragraph(tokens: list[Token], inline_index: int) -> None:
    """Drop the ``<p>`` wrapper around an inline token that rendered empty."""

    if (
        0 < inline_index < len(tokens) - 1
        and tokens[inline_index - 1].type == "paragraph_open"
        and tokens[inline_index + 1].type == "paragraph_close"
    ):
        tokens[inline_index - 1].hidden = True
        tokens[inline_index + 1].hidden = True


def _html_inline(content: str) -> Token:
    token = Token("html_inline", "", 0)
    token.content = content
    return token
