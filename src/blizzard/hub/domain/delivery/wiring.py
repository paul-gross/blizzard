"""The delivery area's registrations: the flow reads, views and writes over the tenant's one flow store, and the
flow-line builder and reconciler over them.

The three flow ports alias the one store the root hands in, so a reader and a writer in one tenant share it; the area
names no adapter. The reconciler's chunk-write refusals are the delivery edge's to provide, since the router that
answers them lives above this area. The store-free graph declares each registration absent, from the same roster."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.delivery.lines import FlowLineFactory
from blizzard.hub.domain.delivery.ports.flows import IFlowStore, IReadFlows, IReadFlowViews, IWriteFlows
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.tenancy import STORE_UNAVAILABLE

#: The flow ports the area aliases onto the store it is handed.
_PORTS: tuple[type[Any], ...] = (IReadFlows, IReadFlowViews, IWriteFlows)

#: The area's services, each registered by class.
_SERVICES: tuple[type[Any], ...] = (FlowLineFactory, FlowLineReconciler)


def register(registry: Registry, *, flows: Ref[IFlowStore]) -> None:
    """Declare the flow ports over the store handed in, and the flow-line builder and reconciler, each once per
    tenant."""
    for each in _PORTS:
        registry.alias(port(each), flows)
    for service in _SERVICES:
        registry.service(service, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports and services absent, for the store-free graph."""
    for missing in (*_PORTS, *_SERVICES):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
