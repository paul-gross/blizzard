"""The auth area's aliases onto the host store adapters it is handed."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Ref, Registry, port
from blizzard.runner.auth.jti_cache import IJtiCache


def register_host_store_ports(
    registry: Registry,
    jti_cache: Ref[IJtiCache],
) -> None:
    """Alias this area's host ports onto the adapters handed by the process root."""
    registry.alias(port(IJtiCache), jti_cache)
