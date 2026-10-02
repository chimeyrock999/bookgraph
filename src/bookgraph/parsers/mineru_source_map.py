"""Map MinerU's non-PDF output back to members of the source package.

MinerU 4 parses EPUB and Office files natively (``flash`` tier) but its result
contract only knows pages: an EPUB's ``page_idx`` is its OPF spine index, and its
images are renamed ``images/page_<p>_image_<i>.<ext>``. The source package is gone
by the time ``document.json`` is built, so :class:`MinerURunner` writes this map
next to the staged middle JSON (``<doc_id>_source_map.json``) while it still has the
source, and :mod:`bookgraph.parsers.mineru_middle_v2` reads it to give every block a
``<source.epub>!<member>`` locator.

The map is plain JSON::

    {"source": "book.epub",
     "spine": ["OEBPS/text/ch01.xhtml", null, ...],  # EPUB only; index = page_idx
     "images": {"images/page_0_image_2.png": "OEBPS/images/fig-1.png"}}

The spine keeps one entry per ``itemref``, as MinerU makes one page per
``itemref`` (an empty page for one whose ``idref`` is not in the manifest, which
maps to ``null`` here), so ``page_idx`` keeps indexing it after a gap. Hrefs are
percent-decoded into package member names, as MinerU decodes them.

An image is matched to its member by identical bytes, so an image MinerU re-encoded
(DOCX pictures come out as JPEG) keeps no member. A DOCX has no spine: MinerU puts
the whole document on ``page_idx`` 0, so ``p0.b<index>`` is its only locator.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree

from bookgraph.utils import MINERU_MIDDLE_JSON_SUFFIX

SOURCE_MAP_SUFFIX = "_source_map.json"

_CONTAINER = "META-INF/container.xml"
_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_OPF_NS = "{http://www.idpf.org/2007/opf}"
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})


def build_source_map(source: Path, images_dir: Path | None) -> dict[str, Any]:
    """The spine and image members of ``source`` for MinerU's staged output.

    ``images_dir`` is the staged ``images/`` directory, whose files are matched to
    the package's image members by content. A source that is not a readable zip
    package still gets a map that names it, with no spine and no images.
    """

    mapping: dict[str, Any] = {"source": source.name, "spine": None, "images": {}}
    try:
        with zipfile.ZipFile(source) as package:
            if source.suffix.lower() == ".epub":
                mapping["spine"] = _epub_spine(package)
            mapping["images"] = _image_members(package, images_dir)
    except (OSError, zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        pass
    return mapping


def write_source_map(mapping: dict[str, Any], path: Path) -> Path:
    path.write_text(json.dumps(mapping, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_source_map(middle_json: Path) -> dict[str, Any] | None:
    """The map staged next to ``<stem>_middle.json``, or ``None`` when there is none."""

    name = middle_json.name
    if name.endswith(MINERU_MIDDLE_JSON_SUFFIX):
        stem = name[: -len(MINERU_MIDDLE_JSON_SUFFIX)]
    else:
        stem = middle_json.stem
    path = middle_json.with_name(f"{stem}{SOURCE_MAP_SUFFIX}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _epub_spine(package: zipfile.ZipFile) -> list[str | None]:
    """Spine members in reading order, as package paths (``OEBPS/text/ch01.xhtml``).

    One entry per ``itemref``; ``None`` for an ``idref`` the manifest does not name.
    """

    container = ElementTree.fromstring(package.read(_CONTAINER))
    rootfile = container.find(f".//{_CONTAINER_NS}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        return []
    opf_path = str(rootfile.get("full-path"))
    opf = ElementTree.fromstring(package.read(opf_path))
    base = posixpath.dirname(opf_path)
    hrefs = {
        item.get("id"): item.get("href")
        for item in opf.iter(f"{_OPF_NS}item")
        if item.get("id") and item.get("href")
    }
    spine: list[str | None] = []
    for itemref in opf.iter(f"{_OPF_NS}itemref"):
        href = hrefs.get(itemref.get("idref"))
        path = unquote(href.split("#", 1)[0]) if href else ""
        spine.append(posixpath.normpath(posixpath.join(base, path)) if path else None)
    return spine


def _image_members(package: zipfile.ZipFile, images_dir: Path | None) -> dict[str, str]:
    if images_dir is None or not images_dir.is_dir():
        return {}
    by_digest: dict[str, str] = {}
    for info in package.infolist():
        if Path(info.filename).suffix.lower() in _IMAGE_SUFFIXES:
            by_digest.setdefault(_digest(package.read(info)), info.filename)
    matched: dict[str, str] = {}
    for image in sorted(images_dir.iterdir()):
        if image.is_file() and (member := by_digest.get(_digest(image.read_bytes()))):
            matched[f"{images_dir.name}/{image.name}"] = member
    return matched


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
