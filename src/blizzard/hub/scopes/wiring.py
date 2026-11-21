"""The hub's scope inputs: the process values the entrypoint borrows into the container, and the tenant's store scope.

Every process value is context, supplied when the process scope opens and never finalized by the container; the tenant's
``StoreScope`` is supplied when its scope opens. This module imports no composition root."""

from __future__ import annotations

from sqlalchemy import Engine

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.tenancy import StoreScope


def register(registry: Registry) -> None:
    """Declare the hub's borrowed process contexts and the tenant's store scope."""
    registry.context(port(Engine), lifetime=Lifetime.PROCESS)
    registry.context(port(HubStoreConnections), lifetime=Lifetime.PROCESS)
    registry.context(port(HubSettingsGraph), lifetime=Lifetime.PROCESS)
    registry.context(port(StoreScope), lifetime=Lifetime.TENANT)
