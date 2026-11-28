"""The transfer area's aliases onto the host store adapters it is handed."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Ref, Registry, port
from blizzard.runner.transfer.rows import IReadHostIngestRepository, IWriteHostIngestRepository


def register_host_store_ports(
    registry: Registry,
    host_ingests: Ref[IWriteHostIngestRepository],
) -> None:
    """Alias this area's host ports onto the adapters handed by the process root."""
    registry.alias(port(IReadHostIngestRepository), host_ingests)
    registry.alias(port(IWriteHostIngestRepository), host_ingests)
