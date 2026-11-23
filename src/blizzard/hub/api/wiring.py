"""The API edge's registrations: each tenant's one chunk-frame loader, over the frame reads and the tenant's broker, and
the hub's version window, the process value a root serving runners supplies.

The store-free graph declares the loader absent, and a root that supplies no window declares the window absent."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.version_window import HubVersionWindow
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def register(registry: Registry) -> None:
    """Declare the tenant's chunk-frame loader, once per tenant."""
    registry.service(ChunkFrameLoader, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the chunk-frame loader absent, for the store-free graph."""
    registry.absent(port(ChunkFrameLoader), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


def register_version_window(registry: Registry) -> Ref[HubVersionWindow]:
    """Declare the hub's version window as the process value the root supplies, handing back its ``Ref``."""
    return registry.context(port(HubVersionWindow), lifetime=Lifetime.PROCESS)


def register_version_window_absent(registry: Registry) -> None:
    """Declare the hub's version window absent, for a root that supplies none."""
    registry.absent(port(HubVersionWindow), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
