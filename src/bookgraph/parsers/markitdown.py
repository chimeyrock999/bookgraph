from __future__ import annotations

import re
import warnings
import zipfile
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

# A Markdown image reference: ``![alt](src)`` or ``![alt](src "title")``. The src stops at
# whitespace or the closing paren, which matches what MarkItDown/markdownify emit.
_IMAGE_REFERENCE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*(?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)"
)


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
        markdown = _stage_epub_assets(source, output_dir, markdown)
        staged = output_dir / f"{doc_id}.md"
        staged.write_text(markdown)

        return document_from_markdown(
            markdown,
            doc_id=doc_id,
            fallback_title=source.stem,
            source_path=str(source),
            parser_name=self.name,
            metadata={"markdown_path": str(staged)},
            # Block line ranges belong to the staged Markdown, so that artifact -
            # not the binary original - is what proves each block.
            block_source_path=str(staged),
        )


def _stage_epub_assets(source: Path, output_dir: Path, markdown: str) -> str:
    """Copy EPUB-referenced images beside the staged Markdown and repoint each reference.

    MarkItDown converts every spine XHTML into Markdown but leaves each ``<img>`` src as the
    zip-relative path it found (e.g. ``assets/ddia_0206.png``) and never extracts the bytes,
    so the reference resolves to nothing under ``sources/parsed/<doc_id>/``. Unpack each
    referenced image into ``output_dir/assets/`` and rewrite the reference to a stable
    ``assets/<name>`` path the downstream asset resolver can open. References that cannot be
    matched to a file in the EPUB are left untouched and reported via a warning, so a lossy
    conversion is surfaced rather than shipped as a silently broken image link.

    A no-op for non-EPUB sources (DOCX/HTML/... carry no separable asset directory) and for
    zips that fail to open, so the base MarkItDown path is unchanged.
    """

    if source.suffix.lower() != ".epub":
        return markdown
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile):
        return markdown

    with archive:
        image_members = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and PurePosixPath(name).suffix.lower() in _IMAGE_SUFFIXES
        ]
        assets_dir = output_dir / "assets"
        staged_by_member: dict[str, str] = {}
        used_names: set[str] = set()
        missing: list[str] = []

        def stage(source_ref: str) -> str | None:
            member = _match_member(source_ref, image_members)
            if member is None:
                return None
            staged = staged_by_member.get(member)
            if staged is None:
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
            return f"![{match.group('alt')}]({staged})"

        rewritten = _IMAGE_REFERENCE.sub(rewrite, markdown)

    if missing:
        unique = sorted(set(missing))
        preview = ", ".join(unique[:5])
        if len(unique) > 5:
            preview += ", ..."
        warnings.warn(
            f"{source.name}: {len(unique)} image reference(s) had no matching asset in the "
            f"EPUB and remain broken links: {preview}",
            stacklevel=2,
        )
    return rewritten


def _match_member(source_ref: str, image_members: list[str]) -> str | None:
    """Find the EPUB member an image reference points at.

    The reference is XHTML-relative and its originating file is lost once MarkItDown flattens
    the spine, so match on the path tail first (``.../assets/ddia_0206.png``), falling back to
    a basename match. ``..`` segments are dropped rather than resolved because there is no
    anchor directory left to resolve them against.
    """

    tail = _clean_reference(source_ref).lstrip("/")
    if not tail:
        return None
    normalized = PurePosixPath(tail)
    parts = [part for part in normalized.parts if part not in ("..", ".")]
    if not parts:
        return None
    suffix = "/".join(parts)
    for member in image_members:
        if member == suffix or member.endswith("/" + suffix):
            return member
    basename = normalized.name.lower()
    for member in image_members:
        if PurePosixPath(member).name.lower() == basename:
            return member
    return None


def _extract_member(
    archive: zipfile.ZipFile, member: str, assets_dir: Path, used_names: set[str]
) -> str:
    """Extract one zip member into ``assets/`` under a collision-free name."""

    name = _unique_name(PurePosixPath(member).name, used_names)
    assets_dir.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as source_file:
        (assets_dir / name).write_bytes(source_file.read())
    return f"assets/{name}"


def _unique_name(name: str, used_names: set[str]) -> str:
    """Reserve ``name`` under ``assets/``, suffixing ``-N`` only on a real basename clash."""

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
