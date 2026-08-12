from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar


class NamedPlugin(Protocol):
    name: str


T = TypeVar("T", bound=NamedPlugin)


@dataclass
class PluginRegistry(Generic[T]):
    """Small name-based registry used by all pluggable BookGraph components."""

    kind: str
    _plugins: dict[str, T] = field(default_factory=dict)

    def register(self, plugin: T) -> None:
        if plugin.name in self._plugins:
            raise ValueError(f"Duplicate {self.kind} plugin: {plugin.name}")
        self._plugins[plugin.name] = plugin

    def get(self, name: str) -> T:
        try:
            return self._plugins[name]
        except KeyError as exc:
            raise KeyError(f"Unknown {self.kind} plugin: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._plugins)

    def all(self) -> Iterable[T]:
        return self._plugins.values()
