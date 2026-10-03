from __future__ import annotations

from bookgraph.models import CanonicalBlock, Document, Section
from bookgraph.parsers.markdown import blocks_from_markdown
from bookgraph.segmenters.heading import HeadingSegmenter
from bookgraph.segmenters.token_page import TokenPageSegmenter
from bookgraph.translation_structure import (
    check_section_translation,
    check_translation_structure,
    describe_structure_issues,
    structural_targets,
)

SOURCE = """\
## Normalization {#sec_datamodels_normalization}

See [the data models chapter](ch03.html#sec_datamodels_normalization) and the
[query figure](#fig_graphql_query "GraphQL query"). Read the [spec][graphql-spec].

![A GraphQL query](images/fig2-1.png)

<a id="fig_graphql_query"></a>
<img src="images/fig2-2.png" alt="Response">

[graphql-spec]: https://spec.graphql.org/ "GraphQL spec"

```text
[not a link](inside-code.html)
```
"""

TRANSLATED = """\
## Chuẩn hoá {#sec_datamodels_normalization}

Xem [chương mô hình dữ liệu](ch03.html#sec_datamodels_normalization) và
[hình truy vấn](#fig_graphql_query "Truy vấn GraphQL"). Đọc [đặc tả][graphql-spec].

![Một truy vấn GraphQL](images/fig2-1.png)

<a id="fig_graphql_query"></a>
<img src="images/fig2-2.png" alt="Phản hồi">

[graphql-spec]: https://spec.graphql.org/ "Đặc tả GraphQL"
"""


def test_translated_labels_with_preserved_targets_pass() -> None:
    assert check_translation_structure(SOURCE, TRANSLATED) == []


def test_structural_targets_cover_links_images_references_html_and_heading_ids() -> None:
    targets = structural_targets(SOURCE)

    assert targets == {
        ("heading_id", "sec_datamodels_normalization"): 1,
        ("link", "ch03.html#sec_datamodels_normalization"): 1,
        ("link", "#fig_graphql_query"): 1,
        ("link", "https://spec.graphql.org/"): 1,
        ("reference", "[graphql-spec]: https://spec.graphql.org/"): 1,
        ("image", "images/fig2-1.png"): 1,
        ("html_id", "fig_graphql_query"): 1,
        ("image", "images/fig2-2.png"): 1,
    }


def test_link_destinations_are_compared_byte_for_byte() -> None:
    source = "See [the chapter](ch03.html#Sec_Models%20One)."
    translated = "Xem [chương](ch03.html#sec_models%20one)."  # "normalised" case

    issues = check_translation_structure(source, translated)

    assert [(i.kind, i.target, i.change) for i in issues] == [
        ("link", "ch03.html#Sec_Models%20One", "missing"),
        ("link", "ch03.html#sec_models%20one", "added"),
    ]


def test_translated_fragment_id_and_heading_anchor_are_reported() -> None:
    source = "# Intro {#sec_intro}\n\nSee [the figure](#fig_query).\n"
    translated = "# Giới thiệu {#sec_gioi_thieu}\n\nXem [hình](#hinh_truy_van).\n"

    issues = check_translation_structure(source, translated)

    assert {(i.kind, i.target, i.change) for i in issues} == {
        ("heading_id", "sec_intro", "missing"),
        ("heading_id", "sec_gioi_thieu", "added"),
        ("link", "#fig_query", "missing"),
        ("link", "#hinh_truy_van", "added"),
    }


def test_translated_reference_identifier_is_reported() -> None:
    source = "Read [the spec][spec].\n\n[spec]: https://example.com/spec\n"
    translated = "Đọc [đặc tả][dac-ta].\n\n[dac-ta]: https://example.com/spec\n"

    issues = check_translation_structure(source, translated)

    assert [(i.kind, i.target, i.change) for i in issues] == [
        ("reference", "[spec]: https://example.com/spec", "missing"),
        ("reference", "[dac-ta]: https://example.com/spec", "added"),
    ]


def test_dropped_link_and_html_id_are_reported_with_counts() -> None:
    source = 'A [x](a.html) and [y](a.html). <span id="note-1">n</span>\n'
    translated = "A x và [y](a.html). n\n"

    issues = check_translation_structure(source, translated)

    assert [(i.kind, i.target, i.change, i.count) for i in issues] == [
        ("link", "a.html", "missing", 1),
        ("html_id", "note-1", "missing", 1),
    ]


def test_html_comments_and_code_are_not_structure() -> None:
    source = 'Text.\n\n<!-- <a href="old.html"> -->\n\n`[x](y.html)`\n'

    assert structural_targets(source) == {}


def test_broken_local_image_may_be_normalised_to_a_resolving_path() -> None:
    source = "![Fig](../broken/fig1.png)\n"
    translated = "![Hình](images/fig1.png)\n"

    def resolves(target: str) -> bool:
        return target == "images/fig1.png"

    assert check_translation_structure(source, translated, asset_resolves=resolves) == []
    # Without a resolver the change is reported.
    assert len(check_translation_structure(source, translated)) == 2


def test_resolving_source_image_stays_pinned_but_carried_figures_may_be_added() -> None:
    issues = check_translation_structure(
        "![Fig](images/fig1.png)\n",
        "![Hình](images/other.png)\n\n![Bảng](images/table1.png)\n",
        asset_resolves=lambda _: True,
    )

    assert [(i.kind, i.target, i.change) for i in issues] == [
        ("image", "images/fig1.png", "missing")
    ]


def test_added_image_that_does_not_resolve_is_reported() -> None:
    issues = check_translation_structure(
        "Text.\n", "Văn bản.\n\n![Hình](images/nope.png)\n", asset_resolves=lambda _: False
    )

    assert [(i.kind, i.change) for i in issues] == [("image", "added")]


def test_translation_only_headings_clustered_before_prose_are_reported() -> None:
    source = """# Data Warehousing

Opening source paragraph.

More source prose.
"""
    translated = """# Kho dữ liệu

### Từ data warehouse đến data lake

### Vượt ra ngoài data lake

Ban đầu, cùng một database...
"""

    issues = check_translation_structure(source, translated)

    assert [(issue.kind, issue.target, issue.change) for issue in issues] == [
        ("heading", "Từ data warehouse đến data lake", "added"),
        ("heading", "Vượt ra ngoài data lake", "added"),
    ]


def test_source_child_headings_allow_translated_headings_before_prose() -> None:
    source = """# Data Warehousing

### From data warehouse to data lake

### Beyond the data lake

Opening source paragraph.
"""
    translated = """# Kho dữ liệu

### Từ data warehouse đến data lake

### Vượt ra ngoài data lake

Ban đầu, cùng một database...
"""

    assert check_translation_structure(source, translated) == []


def test_extra_translation_only_headings_beyond_source_children_are_reported() -> None:
    source = """# T

### Real source child

Source prose.
"""
    translated = """# T

### Translated real child

### Translator-only A

### Translator-only B

Translated prose.
"""

    issues = check_translation_structure(source, translated)

    assert [(issue.kind, issue.target, issue.change) for issue in issues] == [
        ("heading", "Translator-only A", "added"),
        ("heading", "Translator-only B", "added"),
    ]


def test_source_headings_after_opening_prose_do_not_forgive_top_translation_headings() -> None:
    source = """# T

Opening source prose.

### Real child later

Child source prose.
"""
    translated = """# T

### Translator-only top

### Another top

Translated opening prose.
"""

    issues = check_translation_structure(source, translated)

    assert [(issue.kind, issue.target, issue.change) for issue in issues] == [
        ("heading", "Translator-only top", "added"),
        ("heading", "Another top", "added"),
    ]


def test_describe_structure_issues_is_short_and_actionable() -> None:
    issues = check_translation_structure("[a](x.html)\n", "[a](y.html)\n")

    text = describe_structure_issues(issues)

    assert "link 'x.html' missing" in text
    assert "link 'y.html' added" in text


def test_html_attributes_are_read_quote_aware() -> None:
    html = (
        "<img alt='a src=x' src=\"fig.png\"> "
        '<img src="data:image/svg+xml,<svg/>" title="a > b"> '
        "<a href=ch01.html#top name=top>x</a>\n"
    )

    assert structural_targets(html) == {
        ("image", "fig.png"): 1,
        ("image", "data:image/svg+xml,<svg/>"): 1,
        ("link", "ch01.html#top"): 1,
        ("html_id", "top"): 1,
    }


def test_reference_labels_compare_case_insensitively_everywhere() -> None:
    for wrap in ("{}", "> {}", "- {}"):
        source = wrap.format("[Spec]: http://x") + "\n\n[a][Spec]\n"
        translated = wrap.format("[spec]: http://x") + "\n\n[b][spec]\n"
        assert check_translation_structure(source, translated) == [], wrap


def test_html_src_is_an_image_only_on_img() -> None:
    html = '<script src="app.js"></script> <iframe src="embed.html"></iframe> <img src="f.png">\n'

    assert structural_targets(html) == {
        ("link", "app.js"): 1,
        ("link", "embed.html"): 1,
        ("image", "f.png"): 1,
    }
    # A resolving ``src`` on a non-image element is not a carried figure.
    issues = check_translation_structure(
        "x\n", '<img-zoom src="f.png">\n', asset_resolves=lambda _: True
    )
    assert [(i.kind, i.change) for i in issues] == [("link", "added")]


def test_data_image_may_be_added_and_long_targets_are_shortened() -> None:
    payload = "data:image/png;base64," + "A" * 500

    assert (
        check_translation_structure("x\n", f"![f]({payload})\n", asset_resolves=lambda _: False)
        == []
    )
    issues = check_translation_structure(f"![f]({payload})\n", "x\n")
    text = describe_structure_issues(issues)
    assert len(text) < 120
    assert text.startswith("image 'data:image/png;base64,")
    assert "…" in text


def _parsed_section(markdown: str) -> tuple[Section, dict[str, CanonicalBlock]]:
    blocks = blocks_from_markdown(markdown)
    document = Document(doc_id="doc", title="Doc", blocks=blocks)
    (section,) = HeadingSegmenter(target_level=1).segment(document)
    return section, {block.id: block for block in blocks}


CODE_CHAPTER = """# Rendering

Wire it up as in [the guide](guide.html#setup):

```html
<div id="app"><a href="/home">Home</a></div>
```

```markdown
Read [the spec](https://spec.example).
```

    handlers[0](event)
"""


def test_parsed_code_is_not_structure_whether_or_not_the_translation_fences_it() -> None:
    section, blocks = _parsed_section(CODE_CHAPTER)
    assert "```" not in section.text  # the parser strips the fences

    fenced = CODE_CHAPTER.replace("Wire it up as in [the guide]", "Kết nối như [hướng dẫn]")
    unfenced = (
        "# Kết xuất\n\nKết nối như [hướng dẫn](guide.html#setup):\n\n"
        + section.text.split("\n\n", 1)[1]
    )

    for body in (fenced, unfenced):
        assert check_section_translation(section, body, blocks=blocks) == []
    # Real structure next to the code is still checked.
    broken = fenced.replace("guide.html#setup", "huong-dan.html")
    assert {
        (i.target, i.change) for i in check_section_translation(section, broken, blocks=blocks)
    } == {
        ("guide.html#setup", "missing"),
        ("huong-dan.html", "added"),
    }


def test_parser_drops_reference_definitions_so_they_are_not_checked() -> None:
    # Pins a documented limitation: ``[spec]: …`` never reaches ``Section.text``, so a
    # reference-style link in a parsed section is plain text on both sides.
    section, blocks = _parsed_section("# Refs\n\nRead [the spec][spec].\n\n[spec]: https://x\n")

    assert "[spec]:" not in section.text
    assert check_section_translation(section, "Đọc [đặc tả][spec].", blocks=blocks) == []


def test_token_page_sections_keep_their_title_blocks_in_the_source() -> None:
    # The token/page segmenter keeps title blocks in ``Section.text``; the rebuilt source
    # must too, or the heading's link reads as "added" by a faithful translation.
    blocks = blocks_from_markdown(
        "# See [RFC 1](https://rfc.example/1)\n\nBody text.\n\n```\nf(x)\n```\n"
    )
    document = Document(doc_id="doc", title="Doc", blocks=blocks)
    (section,) = TokenPageSegmenter().segment(document)
    by_id = {block.id: block for block in blocks}
    assert "[RFC 1](https://rfc.example/1)" in section.text

    faithful = "Xem [RFC 1](https://rfc.example/1)\n\nNội dung.\n"
    assert check_section_translation(section, faithful, blocks=by_id) == []
    dropped = check_section_translation(section, "Xem RFC 1\n\nNội dung.\n", blocks=by_id)
    assert [(i.target, i.change) for i in dropped] == [("https://rfc.example/1", "missing")]


def test_rebuild_falls_back_to_section_text_when_blocks_disagree() -> None:
    section, blocks = _parsed_section("# A\n\nSee [x](a.html).\n")
    edited = section.model_copy(update={"text": "See [y](b.html)."})

    issues = check_section_translation(edited, "Xem [y](b.html).", blocks=blocks)

    assert issues == []


def test_code_forgiveness_is_by_parsed_target_not_substring() -> None:
    source = 'Call it:\n\n```js\nfetch("/api/users?id=1")\nhandlers[0](event)\n```\n'

    unfenced = 'Gọi nó:\n\nfetch("/api/users?id=1")\nhandlers[0](event)\n'
    assert check_translation_structure(source, unfenced) == []
    for added in ("[API](/api)", "[u](u)", "[e](event) [e2](event)"):
        issues = check_translation_structure(source, f"Gọi nó: {added}\n")
        assert issues and all(i.change == "added" for i in issues), added


def test_long_targets_keep_their_file_name() -> None:
    path = "/workspace/" + "very-long-directory-name/" * 5 + "figure-12.png"
    issues = check_translation_structure("x\n", f"![f]({path})\n")

    text = describe_structure_issues(issues)

    assert "figure-12.png' added" in text
    assert text.startswith("image '/workspace/")
    assert "…" in text
