"""The hub area's aliases onto the host store adapters it is handed."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Ref, Registry, port
from blizzard.runner.hub.host_identity import IReadHostIdentityRepository, IWriteHostIdentityRepository
from blizzard.runner.hub.sealed_tokens import IReadHubTokenRepository, IWriteHubTokenRepository


def register_host_store_ports(
    registry: Registry,
    host_identity: Ref[IWriteHostIdentityRepository],
    hub_tokens: Ref[IWriteHubTokenRepository],
) -> None:
    """Alias this area's host ports onto the adapters handed by the process root."""
    registry.alias(port(IReadHostIdentityRepository), host_identity)
    registry.alias(port(IWriteHostIdentityRepository), host_identity)
    registry.alias(port(IReadHubTokenRepository), hub_tokens)
    registry.alias(port(IWriteHubTokenRepository), hub_tokens)
