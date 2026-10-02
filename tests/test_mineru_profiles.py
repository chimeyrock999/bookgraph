from __future__ import annotations

import pytest

from bookgraph.parsers.mineru_profiles import (
    DEFAULT_PROFILE,
    LEGACY_BACKEND_TIERS,
    VALID_TIERS,
    RemovedMinerUOptionError,
    UnknownMinerUProfileError,
    available_profiles,
    profile_needs_url,
    reject_removed_options,
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


@pytest.mark.parametrize(
    ("profile", "tier"),
    [
        ("fast-text", "flash"),
        ("balanced", "basic"),
        ("local-gpu", "standard"),
        ("remote-gpu", "standard"),
        ("accurate", "advanced"),
    ],
)
def test_profiles_map_onto_mineru_4_tiers(profile: str, tier: str) -> None:
    assert resolve_mineru_options(profile).tier == tier


def test_balanced_default_runs_on_cpu_with_mineru_defaults() -> None:
    options = resolve_mineru_options(DEFAULT_PROFILE)

    assert options.tier == "basic"
    assert options.ocr_mode == "auto"
    assert options.image_analysis is None


def test_none_profile_falls_back_to_balanced() -> None:
    assert resolve_mineru_options(None) == resolve_mineru_options("balanced")


def test_fast_text_profile_reads_the_text_layer_only() -> None:
    options = resolve_mineru_options("fast-text")

    assert options.ocr_mode == "txt"
    assert options.image_analysis is False


def test_only_remote_gpu_needs_a_url() -> None:
    assert [name for name in available_profiles() if profile_needs_url(name)] == ["remote-gpu"]
    assert profile_needs_url(None) is False


def test_explicit_overrides_win_over_profile_defaults() -> None:
    options = resolve_mineru_options(
        "fast-text",
        tier="basic",
        ocr_mode="ocr",
        start_page=2,
        end_page=9,
    )

    assert options.tier == "basic"  # override beats profile's "flash"
    assert options.ocr_mode == "ocr"  # override beats profile's "txt"
    assert options.image_analysis is False  # untouched profile default survives
    assert (options.start_page, options.end_page) == (2, 9)


def test_false_override_is_respected_not_treated_as_unset() -> None:
    options = resolve_mineru_options("accurate", image_analysis=False)

    assert options.image_analysis is False
    assert options.tier == "advanced"


def test_unknown_profile_raises() -> None:
    with pytest.raises(UnknownMinerUProfileError, match="Unknown MinerU profile: turbo"):
        resolve_mineru_options("turbo")


def test_unset_removed_options_pass() -> None:
    reject_removed_options()


@pytest.mark.parametrize(("backend", "tier"), sorted(LEGACY_BACKEND_TIERS.items()))
def test_legacy_backend_names_point_at_their_tier(backend: str, tier: str) -> None:
    assert tier in VALID_TIERS
    with pytest.raises(RemovedMinerUOptionError, match=f"Use --tier {tier}"):
        reject_removed_options(backend=backend)


def test_http_client_backend_hint_mentions_the_remote_url() -> None:
    with pytest.raises(RemovedMinerUOptionError, match="--url for a remote MinerU V1"):
        reject_removed_options(backend="hybrid-http-client")


def test_unknown_backend_is_still_refused_as_removed() -> None:
    with pytest.raises(RemovedMinerUOptionError, match="backend 'gpu' was removed"):
        reject_removed_options(backend="gpu")


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"effort": "high"}, "effort 'high' was removed"),
        ({"formula": True}, "formula toggle was removed"),
        ({"table": False}, "table toggle was removed"),
    ],
)
def test_removed_knobs_fail_with_a_migration_hint(kwargs: dict[str, object], match: str) -> None:
    with pytest.raises(RemovedMinerUOptionError, match=match):
        reject_removed_options(**kwargs)  # type: ignore[arg-type]
