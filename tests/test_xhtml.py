"""HTML fragments re-serialised as well-formed XHTML for EPUB output."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from bookgraph.exports.xhtml import to_xhtml


def _parses(xhtml: str) -> None:
    ET.fromstring(f'<div xmlns="http://www.w3.org/1999/xhtml">{xhtml}</div>')


def test_void_elements_are_self_closed() -> None:
    xhtml, issues = to_xhtml('<p>a<br>b<img src="x.png" alt="y"></p><hr>')

    assert xhtml == '<p>a<br />b<img src="x.png" alt="y" /></p><hr />'
    assert issues == []


def test_text_and_attributes_are_escaped() -> None:
    xhtml, issues = to_xhtml(
        "<p title='say \"hi\" &amp; go'>Tom &amp; Jerry &lt;3&nbsp;x &copy; a < b</p>"
    )

    assert xhtml == (
        '<p title="say &quot;hi&quot; &amp; go">Tom &amp; Jerry &lt;3\xa0x \xa9 a &lt; b</p>'
    )
    assert issues == []
    _parses(xhtml)


def test_implied_end_tags_are_added_without_a_report() -> None:
    xhtml, issues = to_xhtml(
        "<ul><li>a<li>b</ul><p>x<p>y<div>z</div><table><tr><td>1<td>2<tr><th>3</table>"
    )

    assert xhtml == (
        "<ul><li>a</li><li>b</li></ul><p>x</p><p>y</p><div>z</div>"
        "<table><tr><td>1</td><td>2</td></tr><tr><th>3</th></tr></table>"
    )
    assert issues == []


def test_unbalanced_tags_are_repaired_and_reported() -> None:
    xhtml, issues = to_xhtml("<div><span>x</div></p><em>open")

    assert xhtml == "<div><span>x</span></div><em>open</em>"
    assert issues == ["closed unclosed <span>", "dropped stray </p>", "closed unclosed <em>"]
    _parses(xhtml)


def test_scripts_handlers_and_bad_attribute_names_are_dropped() -> None:
    xhtml, issues = to_xhtml(
        '<p onclick="steal()" class="x">a</p>'
        "<script>alert('<p>')</script><style>p{}</style><b 1bad=\"v\">c</b>"
    )

    assert xhtml == '<p class="x">a</p><b>c</b>'
    assert issues == [
        "dropped attribute onclick on <p>",
        "dropped element <script>",
        "dropped element <style>",
        "dropped attribute 1bad on <b>",
    ]
    _parses(xhtml)


def test_elements_html_does_not_define_are_kept_as_text() -> None:
    # Raw XML in a parsed book (an RDF or Avro example) reads as tags to markdown-it.
    xhtml, issues = to_xhtml('<p><rdf:RDF><Person rdf:nodeID="lucy">Lucy</Person></rdf:RDF></p>')

    assert xhtml == (
        "<p>&lt;rdf:RDF&gt;&lt;Person rdf:nodeID=&quot;lucy&quot;&gt;Lucy"
        "&lt;/Person&gt;&lt;/rdf:RDF&gt;</p>"
    )
    assert issues == [
        "kept unknown element <rdf:rdf> as text",
        "kept unknown element <person> as text",
    ]
    _parses(xhtml)


def test_an_unknown_end_tag_alone_is_kept_as_text() -> None:
    # A multi-line start tag can end up in paragraph text and its end tag in raw HTML.
    xhtml, issues = to_xhtml("<p>a</p></rdf:RDF>")

    assert xhtml == "<p>a</p>&lt;/rdf:rdf&gt;"
    assert issues == ["kept unknown element <rdf:rdf> as text"]


def test_quotes_in_text_are_escaped_so_text_never_looks_like_an_attribute() -> None:
    xhtml, _ = to_xhtml('<code>&lt;a href="x" id="y"&gt;</code>')

    assert xhtml == "<code>&lt;a href=&quot;x&quot; id=&quot;y&quot;&gt;</code>"


def test_obsolete_presentational_tags_are_unwrapped() -> None:
    xhtml, issues = to_xhtml("<center><font size=2>small</font></center><p>x</p>")

    assert xhtml == "small<p>x</p>"
    assert issues == ["dropped element <center>", "dropped element <font>"]


def test_comments_are_dropped_and_lang_gets_xml_lang() -> None:
    xhtml, issues = to_xhtml('<!-- note --><div lang="en">a</div><p lang="vi" xml:lang="vi">b</p>')

    assert xhtml == '<div lang="en" xml:lang="en">a</div><p lang="vi" xml:lang="vi">b</p>'
    assert issues == []


def test_self_closed_non_void_element_is_expanded() -> None:
    xhtml, issues = to_xhtml('<a id="x"/><p>t</p>')

    assert xhtml == '<a id="x"></a><p>t</p>'
    assert issues == []


def test_boolean_and_duplicate_attributes() -> None:
    xhtml, _ = to_xhtml('<td nowrap class="a" class="b">x</td>')

    assert xhtml == '<td nowrap="nowrap" class="a">x</td>'


@pytest.mark.parametrize(
    "html",
    [
        "<table><tr><td><p>a</table>",
        "<p><div>x</p></div>",
        "</b></i>text<i><b>both</i></b>",
        "<p>\x0bcontrol\x00 chars</p>",
        "<li>orphan<li>items",
        "<pre>code &lt;x&gt;\n  kept</pre>",
    ],
)
def test_output_is_always_well_formed(html: str) -> None:
    xhtml, _ = to_xhtml(html)

    _parses(xhtml)
