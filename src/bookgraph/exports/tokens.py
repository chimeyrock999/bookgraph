"""Markdown token helpers shared by the export renderings of a translation body."""

from __future__ import annotations

from markdown_it.token import Token


def first_heading_text(tokens: list[Token]) -> str | None:
    """The plain text of a leading heading (``# *Giới thiệu*`` → ``Giới thiệu``).

    Used as a TOC/report title, so Markdown syntax is dropped: only text and code
    spans are kept, and line breaks become spaces.
    """

    if len(tokens) < 2 or tokens[0].type != "heading_open" or tokens[1].type != "inline":
        return None
    parts = []
    for child in tokens[1].children or []:
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
    return " ".join("".join(parts).split()) or None


def shift_headings(tokens: list[Token], level: int) -> None:
    """Re-level headings so the artifact's top heading sits at the section's level."""

    levels = [int(t.tag[1]) for t in tokens if t.type in {"heading_open", "heading_close"}]
    if not levels:
        return
    offset = max(1, min(level, 6)) - min(levels)
    for token in tokens:
        if token.type in {"heading_open", "heading_close"}:
            token.tag = f"h{max(1, min(int(token.tag[1]) + offset, 6))}"
