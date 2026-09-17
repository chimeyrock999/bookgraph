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
from bookgraph.utils import doc_id_from_path, is_url

# Image extensions worth unpacking from an EPUB. Kept broad so an unusual figure format
# (e.g. an SVG diagram) is staged rather than silently dropped as a broken link.
_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff"}
)

# A Markdown image reference: ``![alt](src)`` with an optional ``"title"`` or ``'title'``.
# - alt tolerates escaped brackets and one level of nesting (``![Fig [2-6]](...)``), so a
#   bracketed figure number does not make the whole reference silently fail to match.
# - src is either an angle-bracketed destination (``<my image.png>``, which may contain spaces)
#   or a bare destination that allows one level of balanced parens (``assets/plot(1).png``) — an
#   unencoded ``(`` is a valid URL char tools do not percent-encode, and truncating it would
#   report an existing asset as unresolved.
# - both title quote styles are accepted so a single-quoted title never leaves a ref unstaged.
_IMAGE_REFERENCE = re.compile(
    r"!\[(?P<alt>(?:\\.|\[[^\]]*\]|[^\[\]\\])*)\]"
    r"\(\s*(?:<(?P<src_angle>[^>\n]*)>|(?P<src_bare>(?:[^()\s]|\([^()\s]*\))+))"
    r"(?P<title>\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"
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
    # Stage into a sibling temp dir and swap it in only once the whole set is extracted, so a
    # corrupt member or mid-run disk error never deletes the previous good images or leaves the
    # live ``images/`` half-populated (the repo supports incremental re-ingestion).
    staging_dir = output_dir / ".images.staging"
    shutil.rmtree(staging_dir, ignore_errors=True)
    staged_by_member: dict[str, str] = {}
    used_names: set[str] = set()
    missing: list[str] = []
    extraction_failed = False

    try:
        with archive:
            image_members = [
                name
                for name in archive.namelist()
                if not name.endswith("/")
                and PurePosixPath(name).suffix.lower() in _IMAGE_SUFFIXES
            ]

            def stage(source_ref: str) -> str | None:
                nonlocal extraction_failed
                member = _match_member(source_ref, image_members)
                if member is None:
                    return None
                staged = staged_by_member.get(member)
                if staged is None:
                    try:
                        staged = _extract_member(archive, member, staging_dir, used_names)
                    except (OSError, zipfile.BadZipFile):
                        # A corrupt/unreadable member is treated as unresolved (warned), never a
                        # crash that would abort the whole parse with a raw traceback. Flag it so
                        # the swap below knows this staged set is incomplete.
                        extraction_failed = True
                        return None
                    staged_by_member[member] = staged
                return staged

            def rewrite(match: re.Match[str]) -> str:
                angle = match.group("src_angle")
                src = angle if angle is not None else match.group("src_bare")
                if is_url(src):
                    return match.group(0)
                staged = stage(src)
                if staged is None:
                    # Normalise before recording so ``x.png`` and ``x.png#note`` count once.
                    missing.append(_clean_reference(src))
                    return match.group(0)
                # Preserve any ``"title"`` the source carried; the destination is repointed to the
                # link-safe staged path, so it is always emitted bare (never angle-bracketed).
                return f"![{match.group('alt')}]({staged}{match.group('title') or ''})"

            rewritten = _rewrite_image_references(markdown, rewrite)

        _publish_staged_assets(staging_dir, assets_dir, staged_by_member, extraction_failed)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

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


def _publish_staged_assets(
    staging_dir: Path,
    assets_dir: Path,
    staged_by_member: dict[str, str],
    extraction_failed: bool,
) -> None:
    """Move the freshly staged images into the live ``images/`` directory.

    - Nothing staged: leave ``images/`` as-is. A reference-less or wholly-failed run must not
      wipe the previous good assets down to an empty directory.
    - A complete set (every referenced member extracted): replace ``images/`` atomically, so
      assets renamed or removed since the last parse do not linger as orphans.
    - An incomplete set (a member failed mid-run and was swallowed as unresolved): merge the
      files that did stage in *without* deleting anything, so a transient failure never loses a
      previously-good asset. Any resulting staleness is reclaimed on the next clean re-parse.
    """

    if not staged_by_member:
        return
    if not extraction_failed:
        shutil.rmtree(assets_dir, ignore_errors=True)
        staging_dir.rename(assets_dir)
        return
    assets_dir.mkdir(parents=True, exist_ok=True)
    for staged_rel in staged_by_member.values():
        name = PurePosixPath(staged_rel).name
        (staging_dir / name).replace(assets_dir / name)


def _rewrite_image_references(markdown: str, rewrite: Callable[[re.Match[str]], str]) -> str:
    """Apply ``rewrite`` to every Markdown image reference outside code fences and inline code.

    Rewriting the raw string is simplest, but a naive ``sub`` over the whole document would also
    touch image syntax printed *as an example* inside code — corrupting the sample. Skip fenced
    blocks line-by-line and inline-code spans within a line so only real references are staged.

    Two rare shapes are not recognised — a fuller fix would reuse the token stream
    ``document_from_markdown`` builds, but the coupling is not worth it while markdownify emits
    neither: an inline-code span that straddles a line break (matched per line here), and a
    4-space-indented code block (only ``` ```/~~~ fences are skipped).
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
    # Treat an exact whole-path member and a deeper ``.../<suffix>`` member as competing
    # candidates: the reference is XHTML-relative, so ``images/fig.jpg`` could mean the root
    # member or a chapter-relative one. Only resolve when exactly one candidate exists; a
    # collision is surfaced as unresolved rather than guessed.
    tail_matches = [
        member for member in image_members if member == suffix or member.endswith("/" + suffix)
    ]
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
    # Stream rather than read the whole member into memory: a scanned technical book can carry
    # tens-of-MB raster figures, and copyfileobj keeps the peak flat on image-heavy EPUBs.
    with archive.open(member) as source_file, (assets_dir / name).open("wb") as dest:
        shutil.copyfileobj(source_file, dest)
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
