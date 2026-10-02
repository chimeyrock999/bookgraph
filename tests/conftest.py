from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.documents import write_document
from bookgraph.models import Document
from bookgraph.sections import write_sections
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.workspace import WorkspacePaths
from translated_export_support import DOC, PNG, _blocks


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspacePaths:
    paths = WorkspacePaths(tmp_path)
    document = Document(doc_id=DOC, title="Tiny Book", blocks=_blocks())
    parsed_dir = paths.sources_parsed / DOC
    write_document(document, parsed_dir)
    (parsed_dir / "images").mkdir()
    (parsed_dir / "images" / "fig1.png").write_bytes(PNG)
    sections = HeadingSegmenter(target_level=2).segment(document)
    write_sections(sections, paths.sources_sections / DOC)
    return paths
