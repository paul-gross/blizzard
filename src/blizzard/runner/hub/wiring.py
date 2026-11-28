"""The hub area's aliases onto the host store adapters it is handed."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.runner.hub.client import IHostFactPusher
from blizzard.runner.hub.host_identity import (
    ICurrentHostIdentity,
    IReadHostIdentityRepository,
    IWriteHostIdentityRepository,
    seeded_host_identity,
)
from blizzard.runner.hub.host_registration import HostRegistration, ICurrentHostRegistration
from blizzard.runner.hub.internal.host_infrastructure import (
    HostHubTransport,
    host_hub_client,
    host_registration,
    host_registration_holder,
    open_host_hub_transport,
)
from blizzard.runner.hub.internal.http_hub import HttpHostHubClient
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


def register_host_infrastructure(registry: Registry, *, registered: bool) -> None:
    """The host identity holder, shared by readers and the registration step."""
    identity = registry.factory(seeded_host_identity, lifetime=Lifetime.PROCESS)
    registry.alias(port(ICurrentHostIdentity), identity)
    holder = registry.factory(host_registration_holder, lifetime=Lifetime.PROCESS)
    registry.alias(port(ICurrentHostRegistration), holder)
    client = None
    registration = None
    transport = None
    if registered:
        transport = registry.resource(open_host_hub_transport, lifetime=Lifetime.PROCESS)
        client = registry.factory(host_hub_client, lifetime=Lifetime.PROCESS)
        registry.alias(port(IHostFactPusher), client)
        registration = registry.factory(host_registration, lifetime=Lifetime.PROCESS)
    registry.optional(port(HostHubTransport), transport, lifetime=Lifetime.PROCESS)
    registry.optional(port(HttpHostHubClient), client, lifetime=Lifetime.PROCESS)
    registry.optional(port(HostRegistration), registration, lifetime=Lifetime.PROCESS)
