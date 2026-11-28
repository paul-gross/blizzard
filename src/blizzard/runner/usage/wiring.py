"""The usage area's aliases onto the host store adapters it is handed."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Ref, Registry, port
from blizzard.runner.usage.repository import (
    IReadExternalUsageRepository,
    IReadHostLaneRepository,
    IWriteExternalUsageRepository,
    IWriteHostLaneRepository,
)


def register_host_store_ports(
    registry: Registry,
    external_usage: Ref[IWriteExternalUsageRepository],
    host_lane: Ref[IWriteHostLaneRepository],
) -> None:
    """Alias this area's host ports onto the adapters handed by the process root."""
    registry.alias(port(IReadExternalUsageRepository), external_usage)
    registry.alias(port(IWriteExternalUsageRepository), external_usage)
    registry.alias(port(IReadHostLaneRepository), host_lane)
    registry.alias(port(IWriteHostLaneRepository), host_lane)
