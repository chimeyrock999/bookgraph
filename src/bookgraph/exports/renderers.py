"""Pluggable renderers that turn an assembled export HTML page into a file.

The HTML is self-contained (every image a ``data:`` URI), so a renderer only has
to lay it out. Both PDF backends are optional extras — BookGraph never makes a
heavy layout engine mandatory:

- ``weasyprint`` (``pip install 'bookgraph[pdf]'``; needs the Pango system library);
- ``playwright`` (``pip install 'bookgraph[pdf-chromium]'`` then
  ``playwright install chromium``).

``html`` writes the page itself and needs nothing. Each backend refuses every
non-``data:`` URL, so rendering never reads the filesystem or the network.
"""

from __future__ import annotations

import contextlib
import importlib
import io
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from bookgraph.plugins import PluginRegistry

AUTO_RENDERER = "auto"

# ``auto`` tries these in order for a PDF output.
_PDF_PREFERENCE = ("weasyprint", "playwright")


class RenderError(RuntimeError):
    """A renderer is unavailable or failed to produce its output."""


class ExportRenderer(ABC):
    """Render a self-contained HTML page to an output file."""

    name: str
    # File suffix the renderer produces, e.g. ``.pdf``.
    suffix: str
    # Install hint shown when the backend is not available.
    install_hint: str = ""

    @abstractmethod
    def available(self) -> bool:
        """Whether the backend can run in this environment (cheap, no rendering)."""

    @abstractmethod
    def render(self, html: str, output: Path) -> None:
        raise NotImplementedError


class HtmlRenderer(ExportRenderer):
    name = "html"
    suffix = ".html"

    def available(self) -> bool:
        return True

    def render(self, html: str, output: Path) -> None:
        output.write_text(html, encoding="utf-8")


class WeasyPrintRenderer(ExportRenderer):
    name = "weasyprint"
    suffix = ".pdf"
    install_hint = "pip install 'bookgraph[pdf]' (WeasyPrint also needs the Pango library)"

    def available(self) -> bool:
        return _import_optional("weasyprint") is not None

    def render(self, html: str, output: Path) -> None:
        weasyprint = _import_optional("weasyprint")
        if weasyprint is None:
            raise RenderError(f"WeasyPrint is not available: {self.install_hint}")

        def data_only_fetcher(url: str, *args: Any, **kwargs: Any) -> Any:
            if not url.startswith("data:"):
                raise ValueError(f"export renderer refuses non-data URL: {url}")
            return weasyprint.default_url_fetcher(url, *args, **kwargs)

        try:
            weasyprint.HTML(string=html, url_fetcher=data_only_fetcher).write_pdf(str(output))
        except Exception as exc:  # noqa: BLE001 - surface any backend failure uniformly
            raise RenderError(f"WeasyPrint failed: {exc}") from exc


class PlaywrightRenderer(ExportRenderer):
    name = "playwright"
    suffix = ".pdf"
    install_hint = "pip install 'bookgraph[pdf-chromium]' && playwright install chromium"

    def available(self) -> bool:
        return _import_optional("playwright.sync_api") is not None

    def render(self, html: str, output: Path) -> None:
        sync_api = _import_optional("playwright.sync_api")
        if sync_api is None:
            raise RenderError(f"Playwright is not available: {self.install_hint}")
        try:
            with sync_api.sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    context = browser.new_context(java_script_enabled=False)
                    page = context.new_page()
                    page.route("**/*", lambda route: route.abort())
                    page.set_content(html, wait_until="load")
                    # Page size, margins and the page-number footer come from the
                    # page's own ``@page`` rules, shared with WeasyPrint.
                    page.pdf(path=str(output), print_background=True, prefer_css_page_size=True)
                finally:
                    browser.close()
        except Exception as exc:  # noqa: BLE001 - surface any backend failure uniformly
            raise RenderError(f"Playwright failed: {exc}. {self.install_hint}") from exc


def default_renderer_registry() -> PluginRegistry[ExportRenderer]:
    registry: PluginRegistry[ExportRenderer] = PluginRegistry(kind="export renderer")
    registry.register(WeasyPrintRenderer())
    registry.register(PlaywrightRenderer())
    registry.register(HtmlRenderer())
    return registry


def check_output_suffix(registry: PluginRegistry[ExportRenderer], name: str, output: Path) -> None:
    """Refuse an output path whose suffix does not match the renderer's format.

    Without this, ``--renderer html --out book.pdf`` would write HTML into a
    ``.pdf`` file (and a PDF backend would do the reverse). ``auto`` accepts any
    suffix one of the registered renderers produces, since it picks by suffix.
    """

    suffix = output.suffix.lower()
    if name == AUTO_RENDERER:
        allowed = set().union(*(_accepted_suffixes(r) for r in registry.all()))
        if suffix not in allowed:
            raise RenderError(
                f"Cannot infer the export format from '{output.name}': use one of "
                f"{', '.join(sorted(allowed))}, or pass --renderer."
            )
        return
    renderer = registry.get(name)
    if suffix not in _accepted_suffixes(renderer):
        raise RenderError(
            f"Renderer '{name}' writes {renderer.suffix} files, but the output is "
            f"'{output.name}'. Change --out or --renderer."
        )


def _accepted_suffixes(renderer: ExportRenderer) -> set[str]:
    return {".html", ".htm"} if renderer.suffix == ".html" else {renderer.suffix}


def select_renderer(
    registry: PluginRegistry[ExportRenderer], name: str, output: Path
) -> ExportRenderer:
    """Pick the renderer for ``name`` (or ``auto``) and an output path.

    ``auto`` writes HTML for a ``.html``/``.htm`` output and otherwise the first
    available PDF backend. An explicitly named backend that is not installed is an
    error rather than a silent switch to another engine, and so is an output suffix
    the renderer does not produce (see :func:`check_output_suffix`).
    """

    check_output_suffix(registry, name, output)
    if name != AUTO_RENDERER:
        renderer = registry.get(name)
        if not renderer.available():
            raise RenderError(f"Renderer '{name}' is not available: {renderer.install_hint}")
        return renderer
    if output.suffix.lower() in {".html", ".htm"}:
        return registry.get("html")
    for candidate in _PDF_PREFERENCE:
        try:
            renderer = registry.get(candidate)
        except KeyError:
            continue
        if renderer.available():
            return renderer
    hints = "; ".join(
        f"{r.name}: {r.install_hint}"
        for r in registry.all()
        if r.suffix == ".pdf" and r.install_hint
    )
    raise RenderError(
        "No PDF renderer is available. Install one (" + hints + ") "
        "or export HTML with '--renderer html'."
    )


def _import_optional(module: str) -> Any:
    """Import an optional backend, or ``None`` when it (or a system library) is missing.

    WeasyPrint raises ``OSError`` at import time when Pango is not installed, so the
    guard is broader than ``ImportError``.
    """

    # WeasyPrint also prints a multi-line install banner when that happens; swallow it
    # so probing for an optional backend never pollutes CLI output.
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            return importlib.import_module(module)
    except (ImportError, OSError):
        return None
