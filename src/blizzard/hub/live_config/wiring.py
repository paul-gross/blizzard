"""The live config's registration: each tenant's built-object cache, a resource of the tenant's scope.

The cache owns the configured clients its records build — forge adapters and work-source clients alike — so the tenant
scope's finalizer is the one place they close: on the tenant's eviction, and at hub shutdown while the action loop still
runs. The store-free graph declares the cache absent."""

from __future__ import annotations

from collections.abc import Iterator

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.domain.config.revisions import IReadConfigRevisions
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def config_objects(revisions: IReadConfigRevisions) -> Iterator[ConfigObjectCache]:
    """The tenant's cache over its record revisions, closing every object it built when the tenant's scope closes."""
    objects = ConfigObjectCache(revisions)
    try:
        yield objects
    finally:
        objects.close()


def register(registry: Registry) -> None:
    """Declare the tenant's built-object cache."""
    registry.resource(config_objects, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the cache absent, for the store-free graph."""
    registry.absent(port(ConfigObjectCache), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
