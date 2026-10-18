"""The ``[flags]`` table of a daemon's configuration file, validated to its shape."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from blizzard.foundation.config_keys import ConfigView


def parse_overrides(document: ConfigView, error: type[Exception]) -> Mapping[str, bool]:
    """The ``name → bool`` overrides of ``document``'s ``[flags]`` table, raising ``error`` naming the key for a
    ``flags`` that is not a table or a value that is not a boolean.

    Only the shape is checked: whether a name is a flag, and of which scope, is decided
    where the registry is at hand."""
    if "flags" not in document:
        return MappingProxyType({})
    if not isinstance(document.get("flags"), dict):
        raise error("flags must be a table of flag names to true or false")
    overrides: dict[str, bool] = {}
    for name, value in document.table("flags").items():
        if not isinstance(value, bool):
            raise error(f"[flags] {name} must be true or false, got {value!r}")
        overrides[name] = value
    return MappingProxyType(overrides)
