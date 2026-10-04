"""The requests that make no platform span at all — neither a server span nor any child."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_HEARTBEAT = ("POST", "/api/heartbeat")
_OTLP_SUFFIXES = ("/v1/traces", "/v1/metrics", "/v1/logs")


def is_excluded(scope: Mapping[str, Any]) -> bool:
    """A worker's ``POST /api/heartbeat``, or any OTLP receiver path (``/v1/traces``, ``/v1/metrics``,
    ``/v1/logs``), given its ASGI scope."""
    path = str(scope.get("path", ""))
    if path.rstrip("/").endswith(_OTLP_SUFFIXES):
        return True
    return (str(scope.get("method", "")).upper(), path) == _HEARTBEAT
