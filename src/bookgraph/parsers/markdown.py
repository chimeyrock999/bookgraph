from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin

from bookgraph.models import BlockType, CanonicalBlock, Document
from bookgraph.ports import DocumentParser
from bookgraph.utils import doc_id_from_path

BlockMetadata = dict[str, str | int | float | bool | None]

LIST_OPEN_TO_CLOSE = {
    "bullet_list_open": "bullet_list_close",
    "ordered_list_open": "ordered_list_close",
}


def markdown_reader() -> MarkdownIt:
    """Reader used for every Markdown-shaped source in the pipeline."""

    return MarkdownIt("commonmark").enable("table").use(dollarmath_plugin)


@dataclass
class MarkdownParser(DocumentParser):
    """Parse Markdown sources straight into canonical blocks.

    Local and deterministic: no LLM, no external tool, and nothing written to
    ``output_dir``. Provenance is the source line range, since Markdown has no
    pages.
    """

    name: str = "markdown"

    def parse(self, source: Path, output_dir: Path) -> Document:
        del output_dir  # Markdown parsing produces no side artifacts.
        return document_from_markdown(
            source.read_text(),
            doc_id=doc_id_from_path(source),
            fallback_title=source.stem,
            source_path=str(source),
            parser_name=self.name,
        )


def document_from_markdown(
    text: str,
    *,
    doc_id: str,
    fallback_title: str,
    source_path: str,
    parser_name: str,
    metadata: BlockMetadata | None = None,
    block_source_path: str | None = None,
) -> Document:
    """Build a canonical document from Markdown text.

    Shared by every parser whose adapter produces Markdown rather than blocks.

    ``source_path`` is the document-level origin the user asked for.
    ``block_source_path`` is the artifact that actually proves each block, which
    differs when an adapter converts the original into Markdown first: the block
    line ranges then belong to the converted Markdown, not to the original.
    """

    blocks = blocks_from_markdown(text, source_path=block_source_path or source_path)
    document_metadata: BlockMetadata = {"parser": parser_name, "source_path": source_path}
    if metadata:
        document_metadata.update(metadata)
    return Document(
        doc_id=doc_id,
        title=_document_title(blocks) or fallback_title,
        blocks=blocks,
        metadata=document_metadata,
    )


def blocks_from_markdown(text: str, *, source_path: str | None = None) -> list[CanonicalBlock]:
    tokens = markdown_reader().parse(text)
    blocks: list[CanonicalBlock] = []
    index = 0

    while index < len(tokens):
        token = tokens[index]

        if token.type == "heading_open":
            _append(
                blocks,
                "title",
                _inline_after(tokens, index),
                source_path,
                token,
                level=int(token.tag[1:]),
            )
            index = _end_of_container(tokens, index, "heading_close")
        elif token.type == "paragraph_open":
            end = _end_of_container(tokens, index, "paragraph_close")
            _append_paragraph(blocks, tokens[index + 1], source_path, token)
            index = end
        elif token.type in LIST_OPEN_TO_CLOSE:
            end = _end_of_container(tokens, index, LIST_OPEN_TO_CLOSE[token.type])
            _append(blocks, "list", _render_list(tokens[index:end]), source_path, token)
            index = end
        elif token.type == "table_open":
            end = _end_of_container(tokens, index, "table_close")
            _append(blocks, "table", _render_table(tokens[index:end]), source_path, token)
            index = end
        elif token.type == "blockquote_open":
            end = _end_of_container(tokens, index, "blockquote_close")
            quoted = [item.content for item in tokens[index:end] if item.type == "inline"]
            _append(
                blocks,
                "text",
                "\n\n".join(quoted),
                source_path,
                token,
                metadata={"quote": True},
            )
            index = end
        elif token.type in {"fence", "code_block"}:
            _append(
                blocks,
                "text",
                token.content.strip("\n"),
                source_path,
                token,
                metadata={"code": True, "language": token.info.strip() or None},
            )
            index += 1
        elif token.type == "math_block":
            _append(blocks, "equation", token.content.strip(), source_path, token)
            index += 1
        elif token.type == "html_block":
            _append(blocks, "unknown", token.content.strip(), source_path, token)
            index += 1
        else:
            index += 1

    return blocks


def _append(
    blocks: list[CanonicalBlock],
    block_type: BlockType,
    text: str,
    source_path: str | None,
    token: Token | None = None,
    *,
    level: int | None = None,
    metadata: BlockMetadata | None = None,
) -> None:
    order = len(blocks)
    blocks.append(
        CanonicalBlock(
            id=f"b{order}",
            type=block_type,
            text=text,
            level=level,
            source_path=source_path,
            order=order,
            metadata={**_line_metadata(token), **(metadata or {})},
        )
    )


def _append_paragraph(
    blocks: list[CanonicalBlock],
    inline: Token,
    source_path: str | None,
    token: Token,
) -> None:
    image = _sole_image(inline)
    if image is not None:
        _append(
            blocks,
            "image",
            image.content or str(image.attrGet("alt") or ""),
            source_path,
            token,
            metadata={"src": str(image.attrGet("src") or "")},
        )
        return
    _append(blocks, "text", inline.content, source_path, token)


def _sole_image(inline: Token) -> Token | None:
    """Return the image token when a paragraph carries nothing but that image."""

    children = inline.children or []
    images = [child for child in children if child.type == "image"]
    if len(images) != 1:
        return None
    if any(child.type != "image" and child.content.strip() for child in children):
        return None
    return images[0]


@dataclass
class _ListFrame:
    ordered: bool
    counter: int
    awaiting_item_text: bool = False


def _render_list(tokens: list[Token]) -> str:
    """Render a list container, keeping nesting depth and ordered numbering.

    Flattening a list to plain bullets loses real content: an ordered list is a
    sequence of steps, and indentation carries the parent/child relation.
    """

    lines: list[str] = []
    stack: list[_ListFrame] = []

    for token in tokens:
        if token.type in LIST_OPEN_TO_CLOSE:
            ordered = token.type == "ordered_list_open"
            start = int(token.attrGet("start") or 1) if ordered else 1
            stack.append(_ListFrame(ordered=ordered, counter=start - 1))
        elif token.type in set(LIST_OPEN_TO_CLOSE.values()):
            if stack:
                stack.pop()
        elif token.type == "list_item_open" and stack:
            stack[-1].counter += 1
            stack[-1].awaiting_item_text = True
        elif token.type == "inline" and stack and token.content.strip():
            frame = stack[-1]
            indent = "  " * (len(stack) - 1)
            if frame.awaiting_item_text:
                marker = f"{frame.counter}." if frame.ordered else "-"
                lines.append(f"{indent}{marker} {token.content}")
                frame.awaiting_item_text = False
            else:
                lines.append(f"{indent}  {token.content}")

    return "\n".join(lines)


def _render_table(tokens: list[Token]) -> str:
    rows: list[list[str]] = []
    current: list[str] | None = None
    for token in tokens:
        if token.type == "tr_open":
            current = []
        elif token.type == "tr_close":
            if current:
                rows.append(current)
            current = None
        elif token.type == "inline" and current is not None:
            current.append(token.content)
    return "\n".join(" | ".join(row) for row in rows)


def _end_of_container(tokens: list[Token], start: int, close_type: str) -> int:
    """Index just past the token that closes the container opened at ``start``."""

    open_type = tokens[start].type
    depth = 0
    for offset in range(start, len(tokens)):
        token_type = tokens[offset].type
        if token_type == open_type:
            depth += 1
        elif token_type == close_type:
            depth -= 1
            if depth == 0:
                return offset + 1
    return len(tokens)


def _inline_after(tokens: list[Token], index: int) -> str:
    following = tokens[index + 1] if index + 1 < len(tokens) else None
    return following.content if following is not None and following.type == "inline" else ""


def _line_metadata(token: Token | None) -> BlockMetadata:
    """Source line range as provenance, mirroring page_idx for paged formats."""

    if token is None or token.map is None:
        return {}
    return {"line_start": token.map[0] + 1, "line_end": token.map[1]}


def _document_title(blocks: list[CanonicalBlock]) -> str | None:
    return next(
        (block.text for block in blocks if block.type == "title" and block.level == 1),
        None,
    )
