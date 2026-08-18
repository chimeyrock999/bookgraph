from __future__ import annotations

import pytest

from bookgraph.parsers.mineru_profiles import (
    DEFAULT_PROFILE,
    UnknownMinerUProfileError,
    available_profiles,
    resolve_mineru_options,
)


def test_available_profiles_are_sorted_and_complete() -> None:
    assert available_profiles() == [
        "accurate",
        "balanced",
        "fast-text",
        "local-gpu",
        "remote-gpu",
    ]


def test_balanced_profile_reproduces_stock_mineru_knobs() -> None:
    options = resolve_mineru_options(DEFAULT_PROFILE)

    assert options.method == "auto"
    assert options.backend is None
    assert options.effort is None
    assert options.formula is None
    assert options.table is None
    assert options.image_analysis is None


def test_none_profile_falls_back_to_balanced() -> None:
    assert resolve_mineru_options(None) == resolve_mineru_options("balanced")


def test_fast_text_profile_disables_heavy_passes() -> None:
    options = resolve_mineru_options("fast-text")

    assert options.backend == "pipeline"
    assert options.method == "txt"
    assert options.formula is False
    assert options.table is False
    assert options.image_analysis is False


def test_local_gpu_profile_selects_high_effort_hybrid() -> None:
    options = resolve_mineru_options("local-gpu")

    assert options.backend == "hybrid-engine"
    assert options.effort == "high"
    assert options.table is True


def test_remote_gpu_profile_uses_http_client_backend() -> None:
    options = resolve_mineru_options("remote-gpu", url="http://gpu-box:30000")

    assert options.backend == "hybrid-http-client"
    assert options.url == "http://gpu-box:30000"


def test_explicit_overrides_win_over_profile_defaults() -> None:
    options = resolve_mineru_options(
        "fast-text",
        method="ocr",
        table=True,
        effort="high",
        start_page=2,
        end_page=9,
    )

    assert options.method == "ocr"  # override beats profile's "txt"
    assert options.table is True  # override beats profile's False
    assert options.effort == "high"
    assert options.backend == "pipeline"  # untouched profile default survives
    assert (options.start_page, options.end_page) == (2, 9)


def test_false_override_is_respected_not_treated_as_unset() -> None:
    options = resolve_mineru_options("accurate", table=False, image_analysis=False)

    assert options.table is False
    assert options.image_analysis is False
    assert options.formula is True  # accurate default remains


def test_unknown_profile_raises() -> None:
    with pytest.raises(UnknownMinerUProfileError, match="Unknown MinerU profile: turbo"):
        resolve_mineru_options("turbo")
