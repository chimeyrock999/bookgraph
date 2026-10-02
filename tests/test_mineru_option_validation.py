from __future__ import annotations

import pytest
import typer

from bookgraph.cli.book import _validate_mineru_options
from bookgraph.parsers.mineru_profiles import MinerUOptions


def _options(**overrides: object) -> MinerUOptions:
    base = {
        "tier": "basic",
        "ocr_mode": "auto",
        "image_analysis": None,
        "url": None,
        "start_page": None,
        "end_page": None,
    }
    base.update(overrides)
    return MinerUOptions(**base)  # type: ignore[arg-type]


def test_valid_options_pass() -> None:
    _validate_mineru_options(_options(tier="advanced", ocr_mode="ocr"), pages=10)


def test_unknown_tier_rejected() -> None:
    with pytest.raises(typer.BadParameter, match="Unknown MinerU tier 'gpu'"):
        _validate_mineru_options(_options(tier="gpu"), pages=None)


def test_unknown_ocr_mode_rejected() -> None:
    with pytest.raises(typer.BadParameter, match="Unknown MinerU OCR mode 'txxt'"):
        _validate_mineru_options(_options(ocr_mode="txxt"), pages=None)


def test_remote_profile_needs_url() -> None:
    with pytest.raises(typer.BadParameter, match="needs its URL"):
        _validate_mineru_options(_options(tier="standard"), pages=None, needs_url=True)


def test_remote_profile_with_url_passes() -> None:
    _validate_mineru_options(_options(url="http://gpu-box:8000"), pages=None, needs_url=True)


def test_page_past_end_rejected_when_page_count_known() -> None:
    with pytest.raises(typer.BadParameter, match="past the last page"):
        _validate_mineru_options(_options(end_page=10), pages=10)


def test_last_zero_based_page_is_accepted() -> None:
    _validate_mineru_options(_options(start_page=0, end_page=9), pages=10)


def test_page_bounds_skipped_when_page_count_unknown() -> None:
    _validate_mineru_options(_options(end_page=9999), pages=None)


def test_end_before_start_rejected() -> None:
    with pytest.raises(typer.BadParameter, match="end-page must not be before start-page"):
        _validate_mineru_options(_options(start_page=5, end_page=2), pages=100)
