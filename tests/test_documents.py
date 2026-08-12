from __future__ import annotations

from pathlib import Path

from bookgraph.documents import read_document, write_document
from bookgraph.models import CanonicalBlock, Document


def test_read_document_round_trips_a_written_document(tmp_path: Path) -> None:
    document = Document(
        doc_id="ddia",
        title="Designing Data-Intensive Applications",
        blocks=[
            CanonicalBlock(id="b1", type="title", text="Chapter 3", level=1, page_idx=10),
            CanonicalBlock(id="b2", type="text", text="Opening.", page_idx=10),
        ],
        metadata={"parser": "markdown"},
    )

    path = write_document(document, tmp_path)

    assert read_document(path) == document
