"""Host-lifetime subscription infrastructure."""

from collections.abc import Iterator

from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.runner.subscriptions.internal.usage_http_client import LazyUsageHttpClient


def open_usage_http_client() -> Iterator[LazyUsageHttpClient]:
    """Own the lazy sampler client even when no sampler ever opens its transport."""
    client = LazyUsageHttpClient()
    try:
        yield client
    finally:
        client.close()


def register_host_infrastructure(registry: Registry) -> None:
    registry.resource(open_usage_http_client, lifetime=Lifetime.PROCESS)
