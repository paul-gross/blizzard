"""The settings area's aliases onto the host store adapters it is handed."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Ref, Registry, port
from blizzard.runner.settings.ports import IReadHostSettings, IWriteHostSettings


def register_host_store_ports(
    registry: Registry,
    settings: Ref[IWriteHostSettings],
) -> None:
    """Alias this area's host ports onto the adapters handed by the process root."""
    registry.alias(port(IReadHostSettings), settings)
    registry.alias(port(IWriteHostSettings), settings)
