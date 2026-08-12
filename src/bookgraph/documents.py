from __future__ import annotations

from pathlib import Path

from bookgraph.models import Document


def write_document(document: Document, output_dir: Path) -> Path:
    """Persist a canonical parsed document contract to an output directory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    document_path = output_dir / "document.json"
    document_path.write_text(document.model_dump_json(indent=2) + "\n")
    return document_path
