"""Named MinerU performance/quality profiles and knob resolution.

MinerU exposes a spread of backend/method/effort/feature knobs whose right
setting depends on the hardware and the document. Rather than make every caller
reason about those knobs, BookGraph offers a handful of named *profiles* that
pick sensible defaults, and lets explicit overrides win on top. This module owns
the profile table and the pure resolution logic so it can be unit-tested without
spawning MinerU.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PROFILE = "balanced"


class UnknownMinerUProfileError(ValueError):
    """Raised when a caller asks for a profile that is not defined."""


@dataclass(frozen=True)
class MinerUOptions:
    """Fully resolved MinerU knobs ready to hand to :class:`MinerURunner`."""

    backend: str | None
    method: str
    effort: str | None
    formula: bool | None
    table: bool | None
    image_analysis: bool | None
    url: str | None
    start_page: int | None
    end_page: int | None


@dataclass(frozen=True)
class _ProfileDefaults:
    """Per-profile defaults; ``None`` means "leave MinerU's own default"."""

    backend: str | None = None
    method: str = "auto"
    effort: str | None = None
    formula: bool | None = None
    table: bool | None = None
    image_analysis: bool | None = None


# Profiles name a hardware/quality intent. ``balanced`` is deliberately all-None
# so it reproduces MinerU's stock argv (``-m auto`` only) and keeps the historical
# default behavior unchanged.
PROFILES: dict[str, _ProfileDefaults] = {
    # Digital/text-heavy PDFs on weak GPU / Apple Silicon / CPU: skip the heavy
    # layout/table/formula/image passes and take the fast text path.
    "fast-text": _ProfileDefaults(
        backend="pipeline",
        method="txt",
        effort="medium",
        formula=False,
        table=False,
        image_analysis=False,
    ),
    # Current/default medium-effort path.
    "balanced": _ProfileDefaults(),
    # High-effort hybrid/VLM for the best layout/table/image handling.
    "accurate": _ProfileDefaults(
        backend="hybrid-engine",
        method="auto",
        effort="high",
        formula=True,
        table=True,
        image_analysis=True,
    ),
    # Local VLM/hybrid backend for machines with enough CUDA/VRAM.
    "local-gpu": _ProfileDefaults(
        backend="hybrid-engine",
        method="auto",
        effort="high",
        formula=True,
        table=True,
        image_analysis=True,
    ),
    # External GPU server over the http-client backend; pair with a URL.
    "remote-gpu": _ProfileDefaults(
        backend="hybrid-http-client",
        method="auto",
        effort="medium",
        formula=True,
        table=True,
        image_analysis=True,
    ),
}


def available_profiles() -> list[str]:
    """Profile names in a stable, human-friendly order."""

    return sorted(PROFILES)


def resolve_mineru_options(
    profile: str | None,
    *,
    backend: str | None = None,
    method: str | None = None,
    effort: str | None = None,
    formula: bool | None = None,
    table: bool | None = None,
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

    def pick(override: object, default: object) -> object:
        return override if override is not None else default

    return MinerUOptions(
        backend=pick(backend, defaults.backend),  # type: ignore[arg-type]
        method=pick(method, defaults.method),  # type: ignore[arg-type]
        effort=pick(effort, defaults.effort),  # type: ignore[arg-type]
        formula=pick(formula, defaults.formula),  # type: ignore[arg-type]
        table=pick(table, defaults.table),  # type: ignore[arg-type]
        image_analysis=pick(image_analysis, defaults.image_analysis),  # type: ignore[arg-type]
        url=url,
        start_page=start_page,
        end_page=end_page,
    )
