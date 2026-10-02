"""Registry for generated per-section translations: storage, freshness, and listing.

A reading job that translates sections caches each result so a reset/replay never pays
to retranslate it. The cache keeps the established path convention for the body —
``translations/<lang>/<doc_id>/<section_id>.md`` — and adds a JSON sidecar beside it
(``<section_id>.json``, a :class:`~bookgraph.models.SectionArtifact`) that records what
the translation was generated *from* — the section's content hash and whether its
figures/tables were included — and the hash of the body it describes, so a body later
overwritten by path convention is not vouched for. That sidecar is what turns a path
convention into a registry a workflow can query:

- ``fresh`` — a tracked translation of the section's current content.
- ``stale`` — tracked, but the section changed since (re-segment / re-parse).
- ``untracked`` — a body file with no valid sidecar for it: a cache written before the
  registry existed, or a body overwritten/edited after registration (its hash no longer
  matches the sidecar's ``content_hash``); it may be reused, but its freshness is unknown.
- ``missing`` — no translation body.
- ``orphaned`` — a translation whose section no longer exists (listing only).

The body file is the deliverable. A write removes any previous sidecar, then writes the
body, then the new sidecar, so a crash (or a racing writer) mid-write leaves at worst an
``untracked`` body — never a sidecar vouching for a body it does not describe; the body
hash check backs this up. See ``docs/cli/artifacts.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from bookgraph.models import Section, SectionArtifact
from bookgraph.utils import validate_slug_id
from bookgraph.workspace import WorkspacePaths

TranslationStatus = Literal["fresh", "stale", "untracked", "missing", "orphaned"]

_HASH_PREFIX = "sha256:"


def section_content_hash(section: Section) -> str:
    """Stable fingerprint of the section content a translation is generated from.

    Covers the ``title`` and ``text`` — what a translator actually renders. Ids, page
    spans, and block ids are provenance, not content, so a re-segment that leaves the
    words unchanged keeps existing translations fresh.
    """

    payload = json.dumps(
        {"title": section.title, "text": section.text}, ensure_ascii=False, sort_keys=True
    )
    return _HASH_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def body_content_hash(content: bytes) -> str:
    """Fingerprint of a translation body as stored on disk (UTF-8 bytes)."""

    return _HASH_PREFIX + hashlib.sha256(content).hexdigest()


def validate_lang(lang: str) -> str:
    """Normalise a language tag (``vi``, ``pt-BR``) to a filesystem-safe lowercase slug."""

    return validate_slug_id(lang.strip().lower(), field_name="lang")


def _check_section_id(section_id: str) -> str:
    # Callers validate section ids by membership in the document; this is the last line
    # of defence before the id becomes a filename.
    if not section_id or section_id.startswith(".") or "/" in section_id or "\\" in section_id:
        raise ValueError(f"section_id is not a safe file name: {section_id!r}")
    return section_id


@dataclass(frozen=True)
class TranslationPaths:
    """Where one section's translation body and its registry sidecar live."""

    body: Path
    metadata: Path


def translation_paths(
    workspace: WorkspacePaths, lang: str, doc_id: str, section_id: str
) -> TranslationPaths:
    """Canonical body + sidecar locations for one section's translation."""

    folder = (
        workspace.translations_root
        / validate_lang(lang)
        / validate_slug_id(doc_id, field_name="doc_id")
    )
    stem = _check_section_id(section_id)
    return TranslationPaths(body=folder / f"{stem}.md", metadata=folder / f"{stem}.json")


def _atomic_write(path: Path, text: str) -> None:
    """Write via a same-directory temp file + rename so readers never see a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    # os.open with 0o666 lets the kernel apply the umask (mkstemp would force 0600 and
    # lock a delivery job running as another user out of the cache).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_translation(
    workspace: WorkspacePaths,
    section: Section,
    lang: str,
    content: str,
    *,
    includes_assets: bool = False,
    model: str | None = None,
    created_at: str | None = None,
) -> SectionArtifact:
    """Persist a translation body and register it against the section's current content.

    Overwrites any previous translation of the same section/lang: the registry is a
    cache, and the newest translation of the current content is the one to keep.
    """

    paths = translation_paths(workspace, lang, section.doc_id, section.id)
    artifact = SectionArtifact(
        type="translation",
        lang=validate_lang(lang),
        doc_id=section.doc_id,
        section_id=section.id,
        path=paths.body.relative_to(workspace.root).as_posix(),
        source_section_hash=section_content_hash(section),
        content_hash=body_content_hash(content.encode("utf-8")),
        includes_assets=includes_assets,
        model=model,
        created_at=created_at,
    )
    # Drop the old sidecar, then body, then the new sidecar (the commit record): between
    # the renames the body is untracked, never described by a previous write's sidecar.
    paths.metadata.unlink(missing_ok=True)
    _atomic_write(paths.body, content)
    _atomic_write(paths.metadata, artifact.model_dump_json(indent=2) + "\n")
    return artifact


def _read_metadata(
    paths: TranslationPaths, lang: str, doc_id: str, section_id: str
) -> SectionArtifact | None:
    """The sidecar for a translation, or ``None`` when absent, corrupt, or misplaced.

    Like annotations, a sidecar's location is not trusted: one whose lang/doc/section
    does not match where it sits is ignored, so a copied or stale file can never vouch
    for another section's translation.
    """

    if not paths.metadata.is_file():
        return None
    try:
        artifact = SectionArtifact.model_validate_json(paths.metadata.read_text())
    except (OSError, ValueError):
        return None
    if (artifact.lang, artifact.doc_id, artifact.section_id) != (lang, doc_id, section_id):
        return None
    return artifact


@dataclass(frozen=True)
class TranslationState:
    """One translation's registry status against the section's current content."""

    lang: str
    doc_id: str
    section_id: str
    status: TranslationStatus
    paths: TranslationPaths
    artifact: SectionArtifact | None
    current_section_hash: str | None


def translation_state(
    workspace: WorkspacePaths,
    lang: str,
    doc_id: str,
    section_id: str,
    current_section_hash: str | None,
) -> TranslationState:
    """Resolve a translation's status.

    ``current_section_hash`` is the section's live :func:`section_content_hash`, or
    ``None`` when the section no longer exists — an existing translation is then
    ``orphaned``.
    """

    lang = validate_lang(lang)
    paths = translation_paths(workspace, lang, doc_id, section_id)
    artifact = _read_metadata(paths, lang, doc_id, section_id)
    try:
        body: bytes | None = paths.body.read_bytes()
    except OSError:
        body = None
    if body is None:
        artifact = None
    elif artifact is not None and artifact.content_hash != body_content_hash(body):
        # The body was replaced after registration (path-convention writer, manual edit):
        # the sidecar's provenance no longer describes it.
        artifact = None
    status: TranslationStatus
    if body is None:
        status = "missing"
    elif current_section_hash is None:
        status = "orphaned"
    elif artifact is None:
        status = "untracked"
    elif artifact.source_section_hash == current_section_hash:
        status = "fresh"
    else:
        status = "stale"
    return TranslationState(
        lang=lang,
        doc_id=doc_id,
        section_id=section_id,
        status=status,
        paths=paths,
        artifact=artifact,
        current_section_hash=current_section_hash,
    )


def iter_translation_keys(
    workspace: WorkspacePaths, *, doc_id: str | None = None, lang: str | None = None
) -> Iterator[tuple[str, str, str]]:
    """Yield ``(lang, doc_id, section_id)`` for every translation body on disk, sorted.

    Driven by body files (``*.md``) — a sidecar without its body is not a translation.
    Directories that are not valid slugs are skipped rather than trusted as path parts.
    """

    root = workspace.translations_root
    if not root.is_dir():
        return
    langs = [validate_lang(lang)] if lang is not None else sorted(p.name for p in root.iterdir())
    for lang_name in langs:
        lang_dir = root / lang_name
        if not lang_dir.is_dir() or not _is_slug(lang_name):
            continue
        if doc_id is not None:
            doc_ids = [validate_slug_id(doc_id, field_name="doc_id")]
        else:
            doc_ids = sorted(p.name for p in lang_dir.iterdir())
        for doc_name in doc_ids:
            doc_dir = lang_dir / doc_name
            if not doc_dir.is_dir() or not _is_slug(doc_name):
                continue
            for body in sorted(doc_dir.glob("*.md")):
                if not body.name.startswith("."):
                    yield lang_name, doc_name, body.stem


def _is_slug(value: str) -> bool:
    try:
        validate_slug_id(value)
    except ValueError:
        return False
    return True
