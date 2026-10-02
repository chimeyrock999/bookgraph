"""Find and repair parsed asset references whose file is missing.

A parsed block can point at an image the parser never staged — an EPUB ``<img>`` the
MarkItDown stager could not match, a MinerU figure whose file was deleted, an
``images/`` directory wiped by hand. :mod:`bookgraph.quality` reports such a block as
``asset_file_missing`` and the translated export leaves it out of the reading edition;
this module is the repair path.

For every asset block that :func:`bookgraph.assets.resolve_asset_path` cannot resolve,
the missing file is looked for, in order, in:

1. the parser's own output under ``sources/parsed/<doc_id>/`` (any staged file whose
   path ends with the reference, else whose basename matches it);
2. the original EPUB, when the document was converted from one (the same member
   matching the parse-time stager uses);
3. any extra directories the operator passes (``--from``).

The first source with exactly **one** match wins; a reference several files could
satisfy is reported as ``ambiguous`` rather than guessed, because a wrong figure is
worse than a missing one. A recovered file is copied into ``images/`` and the block is
repointed at ``images/<name>`` — exactly what a successful parse would have written —
keeping the original reference in the block's metadata for provenance. Section text is
untouched, so sections, translations, and their freshness are unaffected.

A reference that cannot be recovered stays as it is: the source evidence is not
rewritten, and the export renders the block's caption without the image.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from bookgraph.assets import asset_reference, resolve_asset_path
from bookgraph.documents import read_document
from bookgraph.models import ASSET_BLOCK_TYPES, CanonicalBlock, Document, Section
from bookgraph.parsers.markitdown import _clean_reference, _match_member, _safe_asset_name
from bookgraph.utils import is_url

# Block metadata keys written on a recovered block, so the repair stays traceable.
ORIGINAL_REFERENCE_KEY = "original_asset_reference"
RECOVERED_FROM_KEY = "asset_recovered_from"

RepairStatus = Literal["recovered", "ambiguous", "unrecoverable"]

_IMAGES_SUBDIR = "images"


class AssetRepair(BaseModel):
    """What happened to one missing asset reference.

    ``recovered_path`` is the new ``images/<name>`` reference and ``recovered_from`` the
    file (or ``<epub>!<member>``) it was copied from, for a ``recovered`` asset.
    ``candidates`` lists the competing matches of an ``ambiguous`` one.
    """

    block_id: str
    section_ids: list[str] = Field(default_factory=list)
    type: str
    reference: str
    status: RepairStatus
    recovered_path: str | None = None
    recovered_from: str | None = None
    candidates: list[str] = Field(default_factory=list)


class AssetRepairReport(BaseModel):
    """Outcome of one repair run over a parsed document."""

    doc_id: str
    dry_run: bool
    missing: int
    recovered: int
    repairs: list[AssetRepair] = Field(default_factory=list)


@dataclass(frozen=True)
class _Match:
    """A candidate file for a reference: on disk, or a member of the source EPUB."""

    label: str
    path: Path | None = None
    archive: Path | None = None
    member: str | None = None


def missing_asset_blocks(document: Document, parsed_dir: Path) -> list[CanonicalBlock]:
    """Asset blocks whose reference does not resolve to a staged file.

    Remote and absolute references are left out: they were never workspace files, so
    there is nothing to recover (the export reports them as remote/missing anyway).
    """

    missing = []
    for block in document.blocks:
        if block.type not in ASSET_BLOCK_TYPES:
            continue
        reference = asset_reference(block)
        if not reference or is_url(reference) or Path(reference).is_absolute():
            continue
        if resolve_asset_path(parsed_dir, block) is None:
            missing.append(block)
    return missing


def repair_document_assets(
    parsed_dir: Path,
    *,
    sections: Sequence[Section] = (),
    search_dirs: Sequence[Path] = (),
    dry_run: bool = False,
) -> tuple[Document, AssetRepairReport]:
    """Recover the missing asset files of ``parsed_dir/document.json``.

    Returns the (possibly) repointed document and the report. Unless ``dry_run``,
    recovered files are copied into ``parsed_dir/images/`` and ``document.json`` is
    rewritten atomically; nothing is written when nothing was recovered.
    """

    document_path = parsed_dir / "document.json"
    document = read_document(document_path)
    owners: dict[str, list[str]] = {}
    for section in sections:
        for block_id in section.block_ids:
            owners.setdefault(block_id, []).append(section.id)

    sources = _Sources(parsed_dir, _source_epub(document), search_dirs)
    images_dir = parsed_dir / _IMAGES_SUBDIR
    reserved = {path.name.lower() for path in _files(images_dir)}
    repairs: list[AssetRepair] = []
    updated: dict[str, CanonicalBlock] = {}
    copied: dict[str, str] = {}  # match label -> images/<name>, so one file is copied once
    for block in missing_asset_blocks(document, parsed_dir):
        reference = asset_reference(block)
        found, candidates = sources.find(reference)
        repair = AssetRepair(
            block_id=block.id,
            section_ids=owners.get(block.id, []),
            type=block.type,
            reference=reference,
            status="ambiguous" if candidates else "unrecoverable",
            candidates=candidates,
        )
        if found is None:
            repairs.append(repair)
            continue
        new_reference = copied.get(found.label)
        if new_reference is None:
            new_reference = _destination(found, images_dir, reserved)
            if not dry_run:
                _copy(found, parsed_dir / new_reference)
            copied[found.label] = new_reference
        updated[block.id] = _repoint(block, new_reference, found.label)
        repairs.append(
            repair.model_copy(
                update={
                    "status": "recovered",
                    "recovered_path": new_reference,
                    "recovered_from": found.label,
                }
            )
        )

    repaired = document.model_copy(
        update={"blocks": [updated.get(block.id, block) for block in document.blocks]}
    )
    if updated and not dry_run:
        _write_atomically(document_path, repaired.model_dump_json(indent=2) + "\n")
    report = AssetRepairReport(
        doc_id=document.doc_id,
        dry_run=dry_run,
        missing=len(repairs),
        recovered=sum(1 for repair in repairs if repair.status == "recovered"),
        repairs=repairs,
    )
    return repaired, report


class _Sources:
    """The ordered places a missing asset may be recovered from."""

    def __init__(self, parsed_dir: Path, epub: Path | None, search_dirs: Sequence[Path]) -> None:
        self._dirs = [parsed_dir, *search_dirs]
        self._epub = epub
        self._file_index: dict[Path, dict[str, list[Path]]] = {}
        self._members: list[str] | None = None

    def find(self, reference: str) -> tuple[_Match | None, list[str]]:
        """The unique match from the first source that has one, else every candidate."""

        candidates: list[str] = []
        for source in self._ordered():
            matches = source(reference)
            if len(matches) == 1:
                return matches[0], []
            candidates.extend(match.label for match in matches)
        return None, candidates

    def _ordered(self) -> list[Callable[[str], list[_Match]]]:
        parser_output, *extra = self._dirs
        sources: list[Callable[[str], list[_Match]]] = [partial(self._on_disk, parser_output)]
        if self._epub is not None:
            sources.append(self._in_epub)
        sources.extend(partial(self._on_disk, directory) for directory in extra)
        return sources

    def _on_disk(self, root: Path, reference: str) -> list[_Match]:
        index = self._file_index.get(root)
        if index is None:
            index = {}
            for path in _files(root):
                index.setdefault(path.name.lower(), []).append(path)
            self._file_index[root] = index
        parts = _reference_parts(reference)
        if not parts:
            return []
        bucket = index.get(parts[-1].lower(), [])
        # Prefer files whose path ends with the whole reference (``assets/x.png``), then
        # fall back to the basename — the same precedence the EPUB stager uses.
        suffix = "/".join(parts).lower()
        tail = [p for p in bucket if p.as_posix().lower().endswith("/" + suffix)]
        return _distinct(tail or bucket)

    def _in_epub(self, reference: str) -> list[_Match]:
        if self._epub is None:
            return []
        if self._members is None:
            try:
                with zipfile.ZipFile(self._epub) as archive:
                    self._members = [n for n in archive.namelist() if not n.endswith("/")]
            except (OSError, zipfile.BadZipFile):
                self._members = []
        member = _match_member(reference, self._members)
        if member is None:
            return []
        return [_Match(label=f"{self._epub}!{member}", archive=self._epub, member=member)]


def _reference_parts(reference: str) -> list[str]:
    cleaned = _clean_reference(reference).lstrip("/")
    return [part for part in PurePosixPath(cleaned).parts if part not in ("..", ".")]


def _files(root: Path) -> Iterable[Path]:
    """Regular files under ``root``, skipping hidden dirs (staging) and symlinks."""

    if not root.is_dir():
        return
    for directory, subdirs, names in os.walk(root, followlinks=False):
        subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
        for name in sorted(names):
            path = Path(directory) / name
            if not path.is_symlink() and path.is_file():
                yield path


def _distinct(paths: list[Path]) -> list[_Match]:
    """One match per distinct file content: identical copies are not an ambiguity."""

    by_digest: dict[str, Path] = {}
    for path in paths:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        by_digest.setdefault(digest, path)
    return [_Match(label=str(path), path=path) for path in by_digest.values()]


def _source_epub(document: Document) -> Path | None:
    source = document.metadata.get("source_path")
    if not isinstance(source, str) or not source.lower().endswith(".epub"):
        return None
    path = Path(source)
    return path if path.is_file() else None


def _destination(found: _Match, images_dir: Path, reserved: set[str]) -> str:
    """The ``images/<name>`` a recovered file is published under.

    A file already inside ``images/`` (the reference was just wrong) keeps its place;
    anything else gets a link-safe name that clashes with nothing staged.
    """

    if found.path is not None and found.path.parent == images_dir:
        return f"{_IMAGES_SUBDIR}/{found.path.name}"
    raw = found.path.name if found.path is not None else PurePosixPath(found.member or "").name
    name = _safe_asset_name(raw)
    stem, suffix = PurePosixPath(name).stem, PurePosixPath(name).suffix
    candidate, counter = name, 1
    while candidate.lower() in reserved:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    reserved.add(candidate.lower())
    return f"{_IMAGES_SUBDIR}/{candidate}"


def _copy(found: _Match, target: Path) -> None:
    if found.path is not None and found.path.resolve() == target.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.partial")
    try:
        if found.path is not None:
            shutil.copyfile(found.path, partial)
        else:
            assert found.archive is not None and found.member is not None
            with zipfile.ZipFile(found.archive) as archive, archive.open(found.member) as src:
                with partial.open("wb") as dest:
                    shutil.copyfileobj(src, dest)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def _repoint(block: CanonicalBlock, reference: str, recovered_from: str) -> CanonicalBlock:
    """Point ``block`` at its recovered file, keeping the parser's original reference."""

    metadata = dict(block.metadata)
    metadata.setdefault(ORIGINAL_REFERENCE_KEY, asset_reference(block))
    metadata[RECOVERED_FROM_KEY] = recovered_from
    update: dict[str, object] = {"metadata": metadata}
    if block.asset_path:
        update["asset_path"] = reference
    elif "src" in block.metadata:
        metadata["src"] = reference
    else:
        metadata["asset_path"] = reference
    return block.model_copy(update=update)


def _write_atomically(path: Path, text: str) -> None:
    partial = path.with_name(f".{path.name}.partial")
    try:
        partial.write_text(text)
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
