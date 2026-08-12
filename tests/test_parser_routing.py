from __future__ import annotations

from pathlib import Path

import pytest

from bookgraph.defaults import default_parser_registry
from bookgraph.parsers.routing import (
    ParserRouter,
    UnsupportedSourceError,
    select_parser_name,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("ddia_middle.json", "mineru-middle-json"),
        ("layout.json", "mineru-middle-json"),
        ("notes.md", "markdown"),
        ("notes.markdown", "markdown"),
        ("Report Q3.DOCX", "markitdown"),
        ("deck.pptx", "markitdown"),
        ("sheet.xlsx", "markitdown"),
        ("page.html", "markitdown"),
        ("plain.txt", "markitdown"),
    ],
)
def test_select_parser_name_routes_source_by_file_type(filename: str, expected: str) -> None:
    assert select_parser_name(Path(filename)) == expected


def test_select_parser_name_requires_an_explicit_choice_for_pdf() -> None:
    with pytest.raises(UnsupportedSourceError, match="MinerU"):
        select_parser_name(Path("ddia.pdf"))


def test_select_parser_name_rejects_unknown_source_types() -> None:
    with pytest.raises(UnsupportedSourceError, match="unsupported source type"):
        select_parser_name(Path("archive.zip"))


def test_default_parser_registry_exposes_every_bundled_adapter() -> None:
    assert default_parser_registry().names() == ["markdown", "markitdown", "mineru-middle-json"]


def test_parser_router_resolves_plugins_from_the_registry() -> None:
    router = ParserRouter()
    registry = default_parser_registry()

    assert router.parser_name_for(Path("notes.md")) == "markdown"
    assert router.parser_for(Path("notes.md"), registry).name == "markdown"
    assert router.parser_for(Path("book_middle.json"), registry).name == "mineru-middle-json"
    assert router.parser_for(Path("deck.pptx"), registry).name == "markitdown"


def test_parser_router_refuses_raw_pdf_instead_of_routing_it_to_mineru() -> None:
    with pytest.raises(UnsupportedSourceError):
        ParserRouter().parser_for(Path("ddia.pdf"), default_parser_registry())
