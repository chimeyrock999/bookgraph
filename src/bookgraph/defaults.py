from __future__ import annotations

from bookgraph.parsers.markdown import MarkdownParser
from bookgraph.parsers.markitdown import MarkItDownParser
from bookgraph.parsers.mineru import MinerUMiddleJsonParser
from bookgraph.plugins import PluginRegistry
from bookgraph.ports import DocumentParser, DocumentSegmenter, WikiBackend
from bookgraph.segmenters.bookmark import BookmarkSegmenter
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.wiki_backends.llmwiki import LlmWikiBackend


def default_parser_registry() -> PluginRegistry[DocumentParser]:
    registry: PluginRegistry[DocumentParser] = PluginRegistry(kind="parser")
    registry.register(MinerUMiddleJsonParser())
    registry.register(MarkdownParser())
    registry.register(MarkItDownParser())
    return registry


def default_segmenter_registry() -> PluginRegistry[DocumentSegmenter]:
    registry: PluginRegistry[DocumentSegmenter] = PluginRegistry(kind="segmenter")
    registry.register(HeadingSegmenter())
    registry.register(BookmarkSegmenter(bookmarks=[]))
    return registry


def default_wiki_backend_registry() -> PluginRegistry[WikiBackend]:
    registry: PluginRegistry[WikiBackend] = PluginRegistry(kind="wiki backend")
    registry.register(LlmWikiBackend())
    return registry
