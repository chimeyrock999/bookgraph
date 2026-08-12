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
    method: str = "auto"
    backend: str | None = None
    timeout_seconds: int | None = 3600


@dataclass(frozen=True)
class WikiConfig:
    backend: str = "llmwiki"


@dataclass(frozen=True)
class BookGraphConfig:
    parsers: ParserConfig = ParserConfig()
    mineru: MinerUConfig = MinerUConfig()
    wiki: WikiConfig = WikiConfig()


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
            method=_string_at(payload, ["mineru", "method"], "auto"),
            backend=_optional_string_at(payload, ["mineru", "backend"]),
            timeout_seconds=_optional_non_negative_int_at(
                payload, ["mineru", "timeout_seconds"], 3600
            ),
        ),
        wiki=WikiConfig(backend=_string_at(payload, ["wiki", "backend"], "llmwiki")),
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


def _optional_non_negative_int_at(
    payload: dict[str, Any], path: list[str], default: int | None
) -> int | None:
    value = _get_nested(payload, path)
    if value is None:
        return default
    if not isinstance(value, int) or value < 0:
        raise typer.BadParameter(f"Config {'.'.join(path)} must be a non-negative integer")
    return None if value == 0 else value
