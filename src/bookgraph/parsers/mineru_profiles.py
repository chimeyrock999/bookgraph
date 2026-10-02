"""Named MinerU performance/quality profiles and knob resolution.

MinerU 4 selects parse quality with a *tier* (``flash | basic | standard |
advanced``) plus an OCR mode, instead of the 3.x backend/method/effort/feature
knobs. BookGraph keeps a handful of named *profiles* that name a hardware intent
and map onto tiers, and lets explicit overrides win on top. This module owns the
profile table, the pure resolution logic, and the guard that turns removed 3.x
knobs into a clear error, so all of it can be unit-tested without spawning MinerU.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

DEFAULT_PROFILE = "balanced"

# MinerU 4 CLI enums. Kept here (the lowest layer that knows the knobs) so both the
# resolver and the CLI validate against one source of truth.
VALID_TIERS = frozenset({"flash", "basic", "standard", "advanced"})
VALID_OCR_MODES = frozenset({"auto", "txt", "ocr"})

# 3.x backend names, kept only to point users at the tier that replaced them.
LEGACY_BACKEND_TIERS: dict[str, str] = {
    "pipeline": "basic",
    "vlm-engine": "standard",
    "hybrid-engine": "standard",
    "vlm-http-client": "standard",
    "hybrid-http-client": "standard",
}

_T = TypeVar("_T")


def first_set(override: _T | None, fallback: _T | None) -> _T | None:
    """Return ``override`` unless it is unset (``None``), then ``fallback``.

    The single "an explicit value wins unless it is None" rule, shared by the
    profile resolver and the CLI's config-over-flag merge so the two layers can
    never drift apart.
    """

    return override if override is not None else fallback


class UnknownMinerUProfileError(ValueError):
    """Raised when a caller asks for a profile that is not defined."""


class RemovedMinerUOptionError(ValueError):
    """Raised when a caller sets a 3.x knob that MinerU 4 no longer has."""


@dataclass(frozen=True)
class MinerUOptions:
    """Fully resolved MinerU knobs ready to hand to :class:`MinerURunner`."""

    tier: str
    ocr_mode: str
    image_analysis: bool | None
    url: str | None
    start_page: int | None
    end_page: int | None


@dataclass(frozen=True)
class _ProfileDefaults:
    """Per-profile defaults; ``None`` means "leave MinerU's own default"."""

    tier: str
    ocr_mode: str = "auto"
    image_analysis: bool | None = None
    needs_url: bool = False


# Profiles name a hardware/quality intent. ``basic`` (small ONNX models on CPU) is
# the default because it keeps the 3.x ``pipeline`` property that matters most: it
# runs anywhere, scanned pages included. ``flash`` reads only the PDF text layer.
PROFILES: dict[str, _ProfileDefaults] = {
    # Born-digital PDFs on any machine: native text layer, no models at all.
    "fast-text": _ProfileDefaults(tier="flash", ocr_mode="txt", image_analysis=False),
    # Default: small models on CPU, OCR when a page needs it.
    "balanced": _ProfileDefaults(tier="basic"),
    # Best layout/table/image quality from the largest VLM tier.
    "accurate": _ProfileDefaults(tier="advanced", image_analysis=True),
    # Local VLM for machines with enough GPU memory.
    "local-gpu": _ProfileDefaults(tier="standard", image_analysis=True),
    # A MinerU V1 parse service elsewhere does the work; pair with a URL.
    "remote-gpu": _ProfileDefaults(tier="standard", image_analysis=True, needs_url=True),
}


def available_profiles() -> list[str]:
    """Profile names in a stable, human-friendly order."""

    return sorted(PROFILES)


def profile_needs_url(profile: str | None) -> bool:
    defaults = PROFILES.get(profile or DEFAULT_PROFILE)
    return defaults is not None and defaults.needs_url


def reject_removed_options(
    *,
    backend: str | None = None,
    effort: str | None = None,
    formula: bool | None = None,
    table: bool | None = None,
) -> None:
    """Fail with a migration hint when a removed MinerU 3.x knob is set.

    MinerU 4 has no backends, effort levels, or per-feature formula/table switches:
    the tier decides them. Silently dropping the knob would change what a user
    asked for, so it is refused with the tier to use instead.
    """

    if backend is not None:
        tier = LEGACY_BACKEND_TIERS.get(backend)
        hint = f" Use --tier {tier}" if tier else " Use --tier"
        remote = (
            " with --url for a remote MinerU V1 service" if backend.endswith("http-client") else ""
        )
        raise RemovedMinerUOptionError(
            f"MinerU backend '{backend}' was removed in MinerU 4.{hint}{remote} "
            f"(tiers: {', '.join(sorted(VALID_TIERS))})."
        )
    if effort is not None:
        raise RemovedMinerUOptionError(
            f"MinerU effort '{effort}' was removed in MinerU 4; the tier sets it "
            "(basic=medium, standard=high, advanced=xhigh). Use --tier."
        )
    for name, value in (("formula", formula), ("table", table)):
        if value is not None:
            raise RemovedMinerUOptionError(
                f"MinerU {name} toggle was removed in MinerU 4; the tier decides "
                f"{name} parsing. Drop --{name}/--no-{name} or [mineru].{name}."
            )


def resolve_mineru_options(
    profile: str | None,
    *,
    tier: str | None = None,
    ocr_mode: str | None = None,
    image_analysis: bool | None = None,
    url: str | None = None,
    start_page: int | None = None,
    end_page: int | None = None,
) -> MinerUOptions:
    """Resolve a profile plus explicit overrides into concrete MinerU knobs.

    Any override that is not ``None`` wins over the profile default. ``url`` and
    the page range are request-scoped rather than profile-scoped, so they pass
    straight through.
    """

    key = profile or DEFAULT_PROFILE
    try:
        defaults = PROFILES[key]
    except KeyError as exc:
        available = ", ".join(available_profiles())
        raise UnknownMinerUProfileError(
            f"Unknown MinerU profile: {key}. Available: {available}"
        ) from exc

    resolved_tier = first_set(tier, defaults.tier)
    resolved_ocr_mode = first_set(ocr_mode, defaults.ocr_mode)
    assert resolved_tier is not None and resolved_ocr_mode is not None
    return MinerUOptions(
        tier=resolved_tier,
        ocr_mode=resolved_ocr_mode,
        image_analysis=first_set(image_analysis, defaults.image_analysis),
        url=url,
        start_page=start_page,
        end_page=end_page,
    )
