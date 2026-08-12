from __future__ import annotations

import pytest

from bookgraph.plugins import PluginRegistry


class ExamplePlugin:
    name = "example"


class AnotherExamplePlugin:
    name = "example"


def test_registry_registers_and_resolves_plugin_by_name() -> None:
    registry: PluginRegistry[ExamplePlugin] = PluginRegistry(kind="parser")

    registry.register(ExamplePlugin())

    assert registry.get("example").name == "example"
    assert registry.names() == ["example"]


def test_registry_rejects_duplicate_plugin_names() -> None:
    registry: PluginRegistry[object] = PluginRegistry(kind="parser")
    registry.register(ExamplePlugin())

    with pytest.raises(ValueError, match="Duplicate parser plugin: example"):
        registry.register(AnotherExamplePlugin())


def test_registry_reports_unknown_plugin_names() -> None:
    registry: PluginRegistry[object] = PluginRegistry(kind="parser")

    with pytest.raises(KeyError, match="Unknown parser plugin: missing"):
        registry.get("missing")
