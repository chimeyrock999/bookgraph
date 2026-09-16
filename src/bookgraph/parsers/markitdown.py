from __future__ import annotations

import re
import shutil
import warnings
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import unquote

from bookgraph.models import Document
from bookgraph.parsers.markdown import document_from_markdown
from bookgraph.ports import DocumentParser
from bookgraph.utils import doc_id_from_path

# Image extensions worth unpacking from an EPUB. Kept broad so an unusual figure format
# (e.g. an SVG diagram) is staged rather than silently dropped as a broken link.
_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff"}
)

# A Markdown image reference: ``![alt](src)`` with an optional ``"title"`` or ``'title'``.
# The src stops at whitespace or the closing paren, matching what MarkItDown/markdownify emit;
# both quote styles are accepted so a single-quoted title never leaves the reference unstaged.
_IMAGE_REFERENCE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*(?P<src>[^)\s]+)(?P<title>\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
)

# Characters that break a bare CommonMark link destination (whitespace splits it into text;
# quotes/brackets/parens are delimiters). Collapsed to ``_`` in staged filenames so the
# rewritten ``![alt](images/<name>)`` always tokenises back into an image whose ``src`` matches
# the file on disk byte-for-byte (angle-bracketed destinations would percent-encode the space
# and no longer resolve).
_UNSAFE_ASSET_CHARS = re.compile(r"[\s()<>\"'\\]+")

# A fenced code block opener/closer (``` or ~~~, 3+, after optional indent) and an inline code
# span. Image references inside code are content — e.g. a chapter teaching Markdown that prints
# ``![figure](assets/x.png)`` in a sample — and must not be rewritten into staged paths.
_CODE_FENCE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(?P<ticks>`+).*?(?P=ticks)")


class MissingParserDependencyError(RuntimeError):
    """Raised when an optional parser dependency is not installed."""


class MarkdownConversion(Protocol):
    text_content: str


class MarkdownConverter(Protocol):
    def convert(self, source: str) -> MarkdownConversion: ...


@dataclass
class MarkItDownParser(DocumentParser):
    """Adapter for Office/HTML/text sources via MarkItDown.

    MarkItDown stays an optional extra: it is imported lazily and the failure is
    reported only when this parser runs, so the base install stays lightweight.
    ``converter`` can be injected, which keeps the adapter testable without the
    dependency.
    """

    converter: MarkdownConverter | None = None
    name: str = "markitdown"

    def parse(self, source: Path, output_dir: Path) -> Document:
        converter = self.converter or _load_markitdown()
        markdown = converter.convert(str(source)).text_content
        doc_id = doc_id_from_path(source)

        output_dir.mkdir(parents=True, exist_ok=True)
        # MarkItDown keeps every EPUB ``<img>`` src verbatim but never unpacks the images
        # from the zip, so the staged Markdown otherwise references files that do not exist.
        markdown, unresolved = _stage_epub_assets(source, output_dir, markdown)
        staged = output_dir / f"{doc_id}.md"
        staged.write_text(markdown)

        metadata: dict[str, str | int | float | bool | None] = {"markdown_path": str(staged)}
        if unresolved:
            # Return the count so the CLI can surface it: a stderr warning alone is swallowed
            # under ``-W ignore`` or a filtered harness, letting ``parse`` report false success.
            metadata["unresolved_image_count"] = len(unresolved)
        return document_from_markdown(
            markdown,
            doc_id=doc_id,
            fallback_title=source.stem,
            source_path=str(source),
            parser_name=self.name,
            metadata=metadata,
            # Block line ranges belong to the staged Markdown, so that artifact -
            # not the binary original - is what proves each block.
            block_source_path=str(staged),
        )


def _stage_epub_assets(source: Path, output_dir: Path, markdown: str) -> tuple[str, list[str]]:
    """Copy EPUB-referenced images beside the staged Markdown and repoint each reference.

    MarkItDown converts every spine XHTML into Markdown but leaves each ``<img>`` src as the
    zip-relative path it found (e.g. ``assets/ddia_0206.png``) and never extracts the bytes,
    so the reference resolves to nothing under ``sources/parsed/<doc_id>/``. Unpack each
    referenced image into ``output_dir/images/`` — the staged-asset directory MinerU also uses
    (``bookgraph.mcp.service._resolve_asset_path`` looks there) — and rewrite the reference to a
    stable ``images/<name>`` path the downstream asset resolver can open. References that cannot
    be matched to exactly one file in the EPUB are left untouched, collected, and reported via a
    warning, so a lossy or ambiguous conversion is surfaced rather than shipped as a silently
    broken (or silently wrong) image. References inside code fences and inline code are left
    alone so a sample like ``![figure](assets/x.png)`` in prose about Markdown is not corrupted.

    Returns the rewritten Markdown and the sorted-unique list of references that could not be
    staged, so the caller can surface the count (a stderr warning alone is easily swallowed).

    Known limitation: an ``<img>`` inside a table cell or heading is rendered by markdownify as
    bare alt-text (no ``![](...)`` syntax) under its default ``keep_inline_images_in=[]``, so
    such a figure vanishes before this pass sees it — it is neither staged nor reported here.

    A no-op for non-EPUB sources (DOCX/HTML/... carry no separable asset directory) and for
    zips that fail to open, so the base MarkItDown path is unchanged.
    """

    if source.suffix.lower() != ".epub":
        return markdown, []
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile):
        return markdown, []

    assets_dir = output_dir / "images"
    # Re-parsing an edited EPUB under the same doc_id must not leave orphaned images behind
    # (the repo supports incremental re-ingestion), so start from a clean staging directory.
    shutil.rmtree(assets_dir, ignore_errors=True)

    with archive:
        image_members = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and PurePosixPath(name).suffix.lower() in _IMAGE_SUFFIXES
        ]
        staged_by_member: dict[str, str] = {}
        used_names: set[str] = set()
        missing: list[str] = []
        dir_ready = False

        def stage(source_ref: str) -> str | None:
            nonlocal dir_ready
            member = _match_member(source_ref, image_members)
            if member is None:
                return None
            staged = staged_by_member.get(member)
            if staged is None:
                if not dir_ready:
                    assets_dir.mkdir(parents=True, exist_ok=True)
                    dir_ready = True
                staged = _extract_member(archive, member, assets_dir, used_names)
                staged_by_member[member] = staged
            return staged

        def rewrite(match: re.Match[str]) -> str:
            src = match.group("src")
            if _looks_remote(src):
                return match.group(0)
            staged = stage(src)
            if staged is None:
                missing.append(src)
                return match.group(0)
            # Preserve any ``"title"`` the source carried; only the destination is repointed.
            return f"![{match.group('alt')}]({staged}{match.group('title') or ''})"

        rewritten = _rewrite_image_references(markdown, rewrite)

    unique_missing = sorted(set(missing))
    if unique_missing:
        preview = ", ".join(unique_missing[:5])
        if len(unique_missing) > 5:
            preview += ", ..."
        warnings.warn(
            f"{source.name}: {len(unique_missing)} image reference(s) had no matching asset in "
            f"the EPUB and remain broken links: {preview}",
            stacklevel=2,
        )
    return rewritten, unique_missing


def _rewrite_image_references(markdown: str, rewrite: Callable[[re.Match[str]], str]) -> str:
    """Apply ``rewrite`` to every Markdown image reference outside code fences and inline code.

    Rewriting the raw string is simplest, but a naive ``sub`` over the whole document would also
    touch image syntax printed *as an example* inside code — corrupting the sample. Skip fenced
    blocks line-by-line and inline-code spans within a line so only real references are staged.
    """

    out: list[str] = []
    fence: str | None = None  # the active fence's marker char, or None outside any fence
    fence_len = 0
    for line in markdown.splitlines(keepends=True):
        opener = _CODE_FENCE.match(line)
        if fence is not None:
            out.append(line)
            marker = opener.group("fence") if opener else ""
            if marker and marker[0] == fence and len(marker) >= fence_len:
                fence = None
            continue
        if opener:
            fence = opener.group("fence")[0]
            fence_len = len(opener.group("fence"))
            out.append(line)
            continue
        out.append(_rewrite_line(line, rewrite))
    return "".join(out)


def _rewrite_line(line: str, rewrite: Callable[[re.Match[str]], str]) -> str:
    """Rewrite image references in one line, leaving inline-code spans untouched."""

    pieces: list[str] = []
    pos = 0
    for code in _INLINE_CODE.finditer(line):
        pieces.append(_IMAGE_REFERENCE.sub(rewrite, line[pos : code.start()]))
        pieces.append(code.group(0))
        pos = code.end()
    pieces.append(_IMAGE_REFERENCE.sub(rewrite, line[pos:]))
    return "".join(pieces)


def _match_member(source_ref: str, image_members: list[str]) -> str | None:
    """Find the single EPUB member an image reference points at, or ``None`` if ambiguous.

    The reference is XHTML-relative and its originating file is lost once MarkItDown flattens
    the spine, so match on the path tail first (``.../assets/ddia_0206.png``), then fall back to
    a basename match. ``..`` segments are dropped rather than resolved because there is no
    anchor directory left to resolve them against.

    A match is only returned when it is *unambiguous* — exactly one member matches. When two
    chapters share an identical relative image path pointing at different files, guessing the
    first would silently show the wrong figure; returning ``None`` surfaces it as an unresolved
    reference (a visible warning) instead, which is easier to notice and debug than a wrong
    image.
    """

    tail = _clean_reference(source_ref).lstrip("/")
    if not tail:
        return None
    normalized = PurePosixPath(tail)
    parts = [part for part in normalized.parts if part not in ("..", ".")]
    if not parts:
        return None
    suffix = "/".join(parts)
    # An exact whole-path match is unambiguous by construction (zip names are unique).
    if suffix in image_members:
        return suffix
    tail_matches = [member for member in image_members if member.endswith("/" + suffix)]
    if tail_matches:
        return tail_matches[0] if len(tail_matches) == 1 else None
    basename = normalized.name.lower()
    base_matches = [
        member for member in image_members if PurePosixPath(member).name.lower() == basename
    ]
    return base_matches[0] if len(base_matches) == 1 else None


def _extract_member(
    archive: zipfile.ZipFile, member: str, assets_dir: Path, used_names: set[str]
) -> str:
    """Extract one zip member into ``images/`` under a collision-free, link-safe name."""

    name = _unique_name(_safe_asset_name(PurePosixPath(member).name), used_names)
    assets_dir.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as source_file:
        (assets_dir / name).write_bytes(source_file.read())
    return f"images/{name}"


def _safe_asset_name(name: str) -> str:
    """Collapse characters that break a bare Markdown link destination into ``_``.

    A staged filename becomes part of the rewritten ``![alt](images/<name>)`` destination, so a
    space or quote in it would either split the link into plain text or force an angle-bracketed
    (percent-encoded) destination that no longer matches the file on disk. Keeping the name
    link-safe lets the reference tokenise straight back into an image whose ``src`` opens the
    real file.
    """

    stem = _UNSAFE_ASSET_CHARS.sub("_", PurePosixPath(name).stem).strip("_")
    suffix = _UNSAFE_ASSET_CHARS.sub("_", PurePosixPath(name).suffix)
    return f"{stem or 'image'}{suffix}"


def _unique_name(name: str, used_names: set[str]) -> str:
    """Reserve ``name`` under ``images/``, suffixing ``-N`` only on a real basename clash."""

    candidate = name or "image"
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate
    stem, suffix = PurePosixPath(candidate).stem, PurePosixPath(candidate).suffix
    counter = 1
    while (candidate := f"{stem}-{counter}{suffix}") in used_names:
        counter += 1
    used_names.add(candidate)
    return candidate


def _clean_reference(source_ref: str) -> str:
    """Drop URL fragment/query and percent-decode so the path can match a zip member."""

    path = source_ref.split("#", 1)[0].split("?", 1)[0]
    return unquote(path).strip()


def _looks_remote(source_ref: str) -> bool:
    return "://" in source_ref or source_ref.startswith("data:")


def _load_markitdown() -> MarkdownConverter:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise MissingParserDependencyError(
            "MarkItDown parser requires the optional parser dependencies. "
            "Install with: uv sync --extra parsers"
        ) from exc
    converter: MarkdownConverter = MarkItDown()
    return converter
