"""The requests that make no platform span at all — neither a server span nor any child."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_HEARTBEAT = ("POST", "/api/heartbeat")
_TRACES_SUFFIX = "/v1/traces"


def is_excluded(scope: Mapping[str, Any]) -> bool:
    """A worker's ``POST /api/heartbeat``, or any ``/v1/traces`` path, given its ASGI scope."""
    path = str(scope.get("path", ""))
    if path.rstrip("/").endswith(_TRACES_SUFFIX):
        return True
    return (str(scope.get("method", "")).upper(), path) == _HEARTBEAT
