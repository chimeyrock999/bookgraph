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
from urllib.parse import unquote

from markdown_it.token import Token

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

# Raw HTML scanning for ``<img>`` tags, attribute by attribute so a ``src=`` or ``>``
# inside another attribute's quoted value is never mistaken for the real one.
_HTML_ATTR = r"""[^\s"'<>/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?"""
_HTML_ATTR_RE = re.compile(r"""([^\s"'<>/=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'=<>`]+))?""")
# ``<img`` followed by whitespace, ``/`` or ``>``: not ``\b``, which would also match
# custom elements such as ``<img-zoom>`` (``\b`` falls between ``g`` and ``-``).
_IMG_OPEN = r"<img(?=[\s/>])"
# Alternatives, in order: an HTML comment (left untouched, so a commented-out image is
# neither embedded nor reported), a well-formed ``<img>`` tag, and a malformed one
# (reported, so it cannot vanish silently under the CSP).
_HTML_IMG_SCAN_RE = re.compile(
    rf"(?P<comment><!--.*?-->)|(?P<img>{_IMG_OPEN}(?:\s+{_HTML_ATTR})*\s*/?>)|(?P<bad>{_IMG_OPEN}[^>]*>)",
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
                token.content = self._rewrite_html_images(
                    token.content, section_id, bases, counter, origin
                )
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
                children = _drop_empty_links(children)
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
        path = self._resolve_link(unquote(src.split("#", 1)[0].split("?", 1)[0]), bases)
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

    def _resolve_link(self, raw: str, bases: list[Path]) -> Path | None:
        """Resolve an image link to a regular file that stays inside the workspace."""

        try:
            root_real = self.workspace.root.resolve()
        except (OSError, ValueError):
            return None
        candidate = Path(raw)
        options = [candidate] if candidate.is_absolute() else [base / candidate for base in bases]
        for option in options:
            try:
                real = option.resolve()
                if real.is_relative_to(root_real) and real.is_file():
                    return real
            except (OSError, ValueError):
                continue
        return None

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

    for attr in _HTML_ATTR_RE.finditer(attributes):
        if attr.group(1).lower() != "src":
            continue
        raw = attr.group(2)
        if raw is None:
            return None
        if raw[:1] in {'"', "'"}:
            raw = raw[1:-1]
        return unescape(raw), attr.span()
    return None


def _has_content(token: Token) -> bool:
    """Whether an inline child renders anything a reader would see.

    Link tags alone render nothing visible; text and raw HTML count only when they
    are not blank.
    """

    if token.type in {"softbreak", "hardbreak", "link_open", "link_close"}:
        return False
    if token.type in {"text", "html_inline"}:
        return bool(token.content.strip())
    return True


def _drop_empty_links(children: list[Token]) -> list[Token]:
    """Remove links left wrapping nothing (``[![fig](gone.png)](url)``).

    Called only after an image was dropped, so an empty, clickable ``<a>`` never
    stands in for the figure.
    """

    empty: set[int] = set()
    opened: list[int] = []
    for index, child in enumerate(children):
        if child.type == "link_open":
            opened.append(index)
        elif child.type == "link_close" and opened:
            start = opened.pop()
            if not any(_has_content(inner) for inner in children[start + 1 : index]):
                empty.update(range(start, index + 1))
    return [child for index, child in enumerate(children) if index not in empty]


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
