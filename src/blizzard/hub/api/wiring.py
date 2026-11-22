"""The API edge's registrations: each tenant's one chunk-frame loader, over the frame reads and the tenant's broker.

The store-free graph declares it absent."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def register(registry: Registry) -> None:
    """Declare the tenant's chunk-frame loader, once per tenant."""
    registry.service(ChunkFrameLoader, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the chunk-frame loader absent, for the store-free graph."""
    registry.absent(port(ChunkFrameLoader), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
