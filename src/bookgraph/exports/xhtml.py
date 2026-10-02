"""Re-serialise an export's HTML fragments as well-formed XHTML (EPUB content).

markdown-it emits HTML, and translation or parser output can carry raw HTML that is
not even balanced (MinerU tables, hand-edited artifacts). An EPUB content document
must be XML, so :func:`to_xhtml` walks a fragment with the standard library's
:class:`html.parser.HTMLParser` and writes it back out: void elements self-closed,
text and attribute values escaped, implied end tags (``p``, ``li``, ``td``, …) added,
and every element closed in order.

Routine fixes are silent. What changes the content is returned as a list of issues,
so the export can report it (``xhtml_repaired``): a stray end tag dropped, an element
left open closed, a ``script``/``style``/… element dropped with its content, an
obsolete presentational tag (``center``, ``font``) or document wrapper dropped (its
content stays), an attribute name XML cannot carry or an event handler dropped, or an
element HTML does not define kept as text. The last is raw XML in a parsed book (an
RDF or Avro example) that markdown-it read as tags: written out escaped, the reader
still sees the example. Comments are always dropped.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param"}
    | {"source", "track", "wbr"}
)
# Dropped with everything inside them: active or page-level content a reading system
# would refuse, or that has no place in a section body.
_DROP_WITH_CONTENT = frozenset(
    {"script", "style", "iframe", "object", "embed", "template", "head", "title"}
)
# Document wrappers and obsolete presentational elements: the tag goes, its content
# stays.
_UNWRAP = frozenset(
    {"html", "body", "meta", "link", "base", "noscript", "center", "font", "basefont"}
    | {"big", "tt", "strike", "nobr", "blink", "marquee"}
)
# The elements HTML defines for a section body; any other name is not markup.
_HTML_ELEMENTS = frozenset(
    {"a", "abbr", "address", "area", "article", "aside", "audio", "b", "bdi", "bdo"}
    | {"blockquote", "br", "button", "canvas", "caption", "cite", "code", "col"}
    | {"colgroup", "data", "datalist", "dd", "del", "details", "dfn", "dialog", "div"}
    | {"dl", "dt", "em", "fieldset", "figcaption", "figure", "footer", "form", "h1"}
    | {"h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "i", "img", "input", "ins"}
    | {"kbd", "label", "legend", "li", "main", "map", "mark", "menu", "meter", "nav"}
    | {"ol", "optgroup", "option", "output", "p", "param", "picture", "pre", "progress"}
    | {"q", "rp", "rt", "ruby", "s", "samp", "section", "select", "small", "source"}
    | {"span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "textarea"}
    | {"tfoot", "th", "thead", "time", "tr", "track", "u", "ul", "var", "video", "wbr"}
)

# Elements whose end tag HTML lets the author leave out: closing them implicitly is a
# routine fix, not a repair worth reporting.
_OPTIONAL_END = frozenset(
    {"p", "li", "dt", "dd", "td", "th", "tr", "thead", "tbody", "tfoot", "option", "rt", "rp"}
)
# A start tag of one of these closes an open ``<p>``.
_CLOSES_P = frozenset(
    {"address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset"}
    | {"figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6"}
    | {"header", "hr", "main", "nav", "ol", "p", "pre", "section", "table", "ul"}
)
# A start tag closes the open elements named here, unless one of the stop elements
# (its container) comes first.
_IMPLIED_CLOSE: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "li": (frozenset({"li"}), frozenset({"ul", "ol"})),
    "dt": (frozenset({"dt", "dd"}), frozenset({"dl"})),
    "dd": (frozenset({"dt", "dd"}), frozenset({"dl"})),
    "td": (frozenset({"td", "th"}), frozenset({"tr", "table"})),
    "th": (frozenset({"td", "th"}), frozenset({"tr", "table"})),
    "tr": (frozenset({"tr", "td", "th"}), frozenset({"table", "thead", "tbody", "tfoot"})),
    "thead": (frozenset({"thead", "tbody", "tfoot", "tr", "td", "th"}), frozenset({"table"})),
    "tbody": (frozenset({"thead", "tbody", "tfoot", "tr", "td", "th"}), frozenset({"table"})),
    "tfoot": (frozenset({"thead", "tbody", "tfoot", "tr", "td", "th"}), frozenset({"table"})),
    "option": (frozenset({"option"}), frozenset({"select", "datalist", "optgroup"})),
}

# Attribute names without a namespace prefix: ``o:gfxdata`` (Word) has no namespace
# declared in a content document.
_XML_NAME = re.compile(r"[A-Za-z_][\w.-]*\Z")
# Characters XML 1.0 cannot carry at all.
_INVALID_XML_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
_TAG_NAME = re.compile(r"<\s*([^\s/>]+)")


def to_xhtml(html: str) -> tuple[str, list[str]]:
    """``html`` as well-formed XHTML, and the repairs that changed its content."""

    builder = _XhtmlBuilder()
    builder.feed(html)
    builder.close()
    return "".join(builder.out), builder.issues


class _XhtmlBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.issues: list[str] = []
        self._open: list[str] = []
        self._dropped: list[str] = []  # names whose start tag was dropped (end tags too)
        self._skip: list[str] = []  # elements being dropped with their content
        self._literal: dict[str, str] = {}  # unknown element names kept as text, as written

    # -- tags -----------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, self_closing=True)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> None:
        if self._skip:
            if tag == self._skip[-1] and not self_closing:
                self._skip.append(tag)
            return
        if tag in _DROP_WITH_CONTENT:
            self.issues.append(f"dropped element <{tag}>")
            if not self_closing and tag not in _VOID:
                self._skip.append(tag)
            return
        if tag in _UNWRAP:
            self.issues.append(f"dropped element <{tag}>")
            if not self_closing and tag not in _VOID:
                self._dropped.append(tag)
            return
        if tag not in _HTML_ELEMENTS:
            raw = self.get_starttag_text() or f"<{tag}>"
            name = _TAG_NAME.match(raw)
            self._literal[tag] = name.group(1) if name else tag
            self.issues.append(f"kept unknown element <{tag}> as text")
            self.out.append(xml_escape(raw))
            return
        self._close_implied(tag)
        rendered = self._attributes(tag, attrs)
        if tag in _VOID:
            self.out.append(f"<{tag}{rendered} />")
        elif self_closing:
            self.out.append(f"<{tag}{rendered}></{tag}>")
        else:
            self.out.append(f"<{tag}{rendered}>")
            self._open.append(tag)

    def _close_implied(self, tag: str) -> None:
        if tag in _CLOSES_P and self._open and self._open[-1] == "p":
            self._pop()
        rule = _IMPLIED_CLOSE.get(tag)
        if rule is None:
            return
        closes, stops = rule
        # The outermost closable element inside the nearest container: a new ``<tr>``
        # closes the open cell and its row.
        target: int | None = None
        for index in range(len(self._open) - 1, -1, -1):
            name = self._open[index]
            if name in stops:
                break
            if name in closes:
                target = index
        if target is not None:
            while len(self._open) > target:
                self._pop(report=self._open[-1] not in _OPTIONAL_END)

    def _attributes(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        seen: set[str] = set()
        parts: list[str] = []
        for name, value in attrs:
            if name in seen:
                continue
            if not _XML_NAME.match(name) and name != "xml:lang":
                self.issues.append(f"dropped attribute {name} on <{tag}>")
                continue
            if name.startswith("on"):
                self.issues.append(f"dropped attribute {name} on <{tag}>")
                continue
            seen.add(name)
            parts.append(f' {name}="{xml_escape(value if value is not None else name)}"')
        lang = dict(attrs).get("lang")
        if lang is not None and "xml:lang" not in seen:
            parts.append(f' xml:lang="{xml_escape(lang)}"')
        return "".join(parts)

    def handle_endtag(self, tag: str) -> None:
        if self._skip:
            if tag == self._skip[-1]:
                self._skip.pop()
            return
        if tag in _VOID:
            return
        if tag in self._dropped:
            self._dropped.remove(tag)
            return
        if tag in self._literal:
            self.out.append(xml_escape(f"</{self._literal[tag]}>"))
            return
        if tag not in _HTML_ELEMENTS and tag not in _UNWRAP and tag not in _DROP_WITH_CONTENT:
            # Its start tag was not in this fragment (a multi-line one read as text):
            # still not markup, so the reader sees it as written, lowercased.
            self.issues.append(f"kept unknown element <{tag}> as text")
            self.out.append(xml_escape(f"</{tag}>"))
            return
        if tag not in self._open:
            self.issues.append(f"dropped stray </{tag}>")
            return
        while self._open[-1] != tag:
            self._pop(report=self._open[-1] not in _OPTIONAL_END)
        self._pop()

    def _pop(self, *, report: bool = False) -> None:
        tag = self._open.pop()
        if report:
            self.issues.append(f"closed unclosed <{tag}>")
        self.out.append(f"</{tag}>")

    def close(self) -> None:
        super().close()
        while self._open:
            self._pop(report=self._open[-1] not in _OPTIONAL_END)

    # -- content --------------------------------------------------------------------

    def handle_data(self, data: str) -> None:
        if not self._skip:
            # Quotes too: text then never holds an attribute-like ``name="…"``, so the
            # EPUB writer's scans of the serialised markup only ever match real tags.
            self.out.append(xml_escape(data, quote=False).replace('"', "&quot;"))

    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return


def xml_escape(text: str, *, quote: bool = True) -> str:
    """``text`` escaped for XML, without the characters XML cannot carry."""

    return escape(_clean(text), quote=quote)


def _clean(text: str) -> str:
    return _INVALID_XML_CHARS.sub("", text)
