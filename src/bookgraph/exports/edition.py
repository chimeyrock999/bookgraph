"""The assembled reading edition that every export writer receives."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from bookgraph.exports.models import BilingualLayout, ExportReport
from bookgraph.exports.outline import OutlineNode
from bookgraph.exports.render import document_html


@dataclass(frozen=True)
class TranslatedExport:
    """The assembled reading edition: its structure, each section's final HTML (links
    resolved, bilingual pairs laid out in ``bilingual_layout``), and its report.

    ``html`` is the whole book as one self-contained page, which the HTML/PDF renderers
    lay out; a writer such as EPUB uses the structure instead
    (:class:`~bookgraph.exports.renderers.ExportWriter`).
    """

    report: ExportReport
    outline: list[OutlineNode]
    bodies: dict[str, str]
    bilingual_layout: BilingualLayout = "columns"

    @cached_property
    def html(self) -> str:
        return document_html(
            self.report, self.outline, self.bodies, show_status=self.report.show_status
        )
