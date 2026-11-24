"""The graph area's registrations: its read, write, body and executor ports over the adapter it is handed, and its
services.

The ports alias the one adapter instance the root hands in, so every graph reader and writer in one tenant shares it;
the area names no adapter. The store-free graph declares each of them absent."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.graph.authoring import DefaultGraphService, GraphMintService
from blizzard.hub.domain.graph.lifecycle import GraphLifecycleService
from blizzard.hub.domain.graph.model import (
    IReadGraphBodies,
    IReadGraphRepository,
    IReadManyGraphs,
    IReadNodeExecutors,
    IWriteGraphRepository,
)
from blizzard.hub.tenancy import STORE_UNAVAILABLE

_PORTS = (IReadGraphRepository, IReadManyGraphs, IWriteGraphRepository, IReadGraphBodies, IReadNodeExecutors)
_SERVICES = (GraphMintService, DefaultGraphService, GraphLifecycleService)


def register(registry: Registry, graphs: Ref[IWriteGraphRepository]) -> None:
    """Declare the graph ports over ``graphs`` and the mint, default-graph and lifecycle services, each once per
    tenant."""
    for each in _PORTS:
        registry.alias(port(each), graphs)
    for service in _SERVICES:
        registry.service(service, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports and services absent, for the store-free graph."""
    for missing in (*_PORTS, *_SERVICES):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
