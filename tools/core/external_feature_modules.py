"""Literal lazy import adapters for contract-declared external features.

The installation contract remains the feature/dependency SSoT.  This module is
the deliberately small executable adapter surface: literal targets keep local
source dependency closure deterministic, while the installation validator
requires exact parity with every default-profile module declared by the SSoT.
"""

from __future__ import annotations

import importlib
from types import MappingProxyType
from typing import Any, Callable, Mapping


_MODULE_LOADERS: Mapping[str, Callable[[], Any]] = MappingProxyType(
    {
        "mcp.server.fastmcp": lambda: importlib.import_module("mcp.server.fastmcp"),
        "watchdog.events": lambda: importlib.import_module("watchdog.events"),
        "rich": lambda: importlib.import_module("rich"),
        "json_repair": lambda: importlib.import_module("json_repair"),
    }
)


def declared_external_module_targets() -> tuple[str, ...]:
    """Return the exact literal adapter inventory for contract validation."""

    return tuple(_MODULE_LOADERS)


def import_declared_external_module(module_name: str) -> Any:
    """Lazily import one known external module; reject undeclared targets."""

    normalized = str(module_name or "")
    loader = _MODULE_LOADERS.get(normalized)
    if loader is None:
        raise ValueError(f"No literal external-module adapter is declared for: {normalized!r}")
    return loader()
