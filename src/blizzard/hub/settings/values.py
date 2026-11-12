"""Freeze JSON-shaped values in snapshots and restore them for validation."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast


def freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): freeze(v) for k, v in cast(Mapping[object, object], value).items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in cast(tuple[object, ...] | list[object], value))
    return value


def thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): thaw(v) for k, v in cast(Mapping[object, object], value).items()}
    if isinstance(value, (list, tuple)):
        return [thaw(v) for v in cast(tuple[object, ...] | list[object], value)]
    return value
