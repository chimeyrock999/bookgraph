from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback guard
    tomllib = None  # type: ignore[assignment]

import typer

from bookgraph.workspace import WorkspacePaths


@dataclass(frozen=True)
class ParserConfig:
    default_pdf: str = "mineru-middle-json"
    default_office: str = "markitdown"
    default_markdown: str = "markdown"


@dataclass(frozen=True)
class MinerUConfig:
    runner: str = "mineru"
    command: str = "mineru"
    profile: str = "balanced"
    method: str | None = None
    backend: str | None = None
    effort: str | None = None
    formula: bool | None = None
    table: bool | None = None
    image_analysis: bool | None = None
    url: str | None = None
    start_page: int | None = None
    end_page: int | None = None
    timeout_seconds: int | None = 3600


@dataclass(frozen=True)
class SegmenterConfig:
    default: str = "heading"
    target_level: int = 2
    max_tokens: int = 800


@dataclass(frozen=True)
class WikiConfig:
    backend: str = "llmwiki"


@dataclass(frozen=True)
class ReadingPlanConfig:
    daily_sections: int = 1


@dataclass(frozen=True)
class BookGraphConfig:
    parsers: ParserConfig = ParserConfig()
    mineru: MinerUConfig = MinerUConfig()
    segmenter: SegmenterConfig = SegmenterConfig()
    wiki: WikiConfig = WikiConfig()
    reading_plan: ReadingPlanConfig = ReadingPlanConfig()


def load_config(workspace: WorkspacePaths) -> BookGraphConfig:
    """Load optional workspace config, falling back to the default init contract."""

    if not workspace.config.is_file():
        return BookGraphConfig()
    if tomllib is None:  # pragma: no cover
        raise typer.BadParameter("Python tomllib is required to read bookgraph.toml")
    try:
        payload = tomllib.loads(workspace.config.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise typer.BadParameter(f"Invalid workspace config: {workspace.config}: {exc}") from exc

    return BookGraphConfig(
        parsers=ParserConfig(
            default_pdf=_string_at(payload, ["parsers", "default_pdf"], "mineru-middle-json"),
            default_office=_string_at(payload, ["parsers", "default_office"], "markitdown"),
            default_markdown=_string_at(payload, ["parsers", "default_markdown"], "markdown"),
        ),
        mineru=MinerUConfig(
            runner=_string_at(payload, ["mineru", "runner"], "mineru"),
            command=_string_at(payload, ["mineru", "command"], "mineru"),
            profile=_string_at(payload, ["mineru", "profile"], "balanced"),
            method=_optional_string_at(payload, ["mineru", "method"]),
            backend=_optional_string_at(payload, ["mineru", "backend"]),
            effort=_optional_string_at(payload, ["mineru", "effort"]),
            formula=_optional_bool_at(payload, ["mineru", "formula"]),
            table=_optional_bool_at(payload, ["mineru", "table"]),
            image_analysis=_optional_bool_at(payload, ["mineru", "image_analysis"]),
            url=_optional_url_at(payload, ["mineru", "url"]),
            start_page=_optional_page_int_at(payload, ["mineru", "start_page"]),
            end_page=_optional_page_int_at(payload, ["mineru", "end_page"]),
            timeout_seconds=_optional_non_negative_int_at(
                payload, ["mineru", "timeout_seconds"], 3600
            ),
        ),
        segmenter=SegmenterConfig(
            default=_string_at(payload, ["segmenter", "default"], "heading"),
            target_level=_positive_int_at(payload, ["segmenter", "target_level"], 2),
            max_tokens=_positive_int_at(payload, ["segmenter", "max_tokens"], 800),
        ),
        wiki=WikiConfig(backend=_string_at(payload, ["wiki", "backend"], "llmwiki")),
        reading_plan=ReadingPlanConfig(
            daily_sections=_positive_int_at(payload, ["reading_plan", "daily_sections"], 1)
        ),
    )


def _get_nested(payload: dict[str, Any], path: list[str]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _string_at(payload: dict[str, Any], path: list[str], default: str) -> str:
    value = _get_nested(payload, path)
    if value is None:
        return default
    if not isinstance(value, str) or not value:
        raise typer.BadParameter(f"Config {'.'.join(path)} must be a non-empty string")
    return value


def _optional_string_at(payload: dict[str, Any], path: list[str]) -> str | None:
    value = _get_nested(payload, path)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise typer.BadParameter(f"Config {'.'.join(path)} must be a non-empty string")
    return value


def _optional_url_at(payload: dict[str, Any], path: list[str]) -> str | None:
    """Like ``_optional_string_at`` but treats an empty string as "unset"."""

    value = _get_nested(payload, path)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise typer.BadParameter(f"Config {'.'.join(path)} must be a string")
    return value


def _optional_bool_at(payload: dict[str, Any], path: list[str]) -> bool | None:
    value = _get_nested(payload, path)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise typer.BadParameter(f"Config {'.'.join(path)} must be a boolean")
    return value


def _optional_page_int_at(payload: dict[str, Any], path: list[str]) -> int | None:
    """A 0-based MinerU page index: absent or a non-negative integer."""

    value = _get_nested(payload, path)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise typer.BadParameter(f"Config {'.'.join(path)} must be a non-negative integer")
    return value


def _optional_non_negative_int_at(
    payload: dict[str, Any], path: list[str], default: int | None
) -> int | None:
    value = _get_nested(payload, path)
    if value is None:
        return default
    if not isinstance(value, int) or value < 0:
        raise typer.BadParameter(f"Config {'.'.join(path)} must be a non-negative integer")
    return None if value == 0 else value


def _positive_int_at(payload: dict[str, Any], path: list[str], default: int) -> int:
    value = _get_nested(payload, path)
    if value is None:
        return default
    if not isinstance(value, int) or value < 1:
        raise typer.BadParameter(f"Config {'.'.join(path)} must be a positive integer")
    return value
