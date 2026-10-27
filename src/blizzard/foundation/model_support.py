"""The one rule deciding whether a native model name is covered by a supported-models list.

The runner applies it to resolve a native name and the hub applies it to match a strict session entry against what a
runner reported, so the two cannot disagree (``bzh:shared-kernel``). Pure: it imports nothing and holds no state."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase

from blizzard.foundation.node_steps import TIER_PREFIX


def model_supported(entry: str, supported: Iterable[str]) -> bool:
    """Whether the native name ``entry`` matches any exact name or glob pattern in ``supported``.

    Case-sensitive, and ``*`` crosses ``/``. A ``blizzard:`` tier entry never matches: a tier is satisfied only by a
    mapping, so an operator pattern such as ``*`` cannot make an unmapped tier look eligible."""
    if entry.startswith(TIER_PREFIX):
        return False
    return any(fnmatchcase(entry, pattern) for pattern in supported)


__all__ = ["model_supported"]
