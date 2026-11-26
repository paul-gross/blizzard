"""A stand-in for the hub process's composition root: the hub's own registration, with the permits area added.

The permit adapter's ``Ref`` goes straight to the area that aliases it, so no other module names the adapter."""

# exemplar: bzh:dependency-injection — production adds the two registration lines to ``register_hub`` itself.

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.clock import IClock
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.hub import process_root as hub
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.store.errors import HubStoreConnections
from exemplar.hub.domain.permits import wiring as permits
from exemplar.hub.store import wiring as store


def open_store_backed(
    connections: HubStoreConnections,
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
) -> hub.HubProcess:
    """The hub's store-backed process with the permits area, registered and opened over the borrowed values."""
    registry = hub.registered(store_backed=True)
    permits.register(registry, store.register_permits(registry))
    return hub.build_store_backed(
        registry,
        connections,
        clock=clock,
        config=config,
        platform_tracing=platform_tracing,
        environ=environ,
        actions=actions,
    )
