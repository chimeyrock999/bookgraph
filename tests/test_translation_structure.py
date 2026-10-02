from __future__ import annotations

from bookgraph.translation_structure import (
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
