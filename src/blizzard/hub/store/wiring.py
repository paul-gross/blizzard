"""The hub store's adapter registrations: each adapter an adopted area reads, registered once at the tenant scope.

Each function registers one adapter and hands its ``Ref`` back, so the area that consumes the adapter aliases its own
ports onto that one instance; the store-free graph declares each of them absent instead."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def register_findings(registry: Registry) -> Ref[FindingStore]:
    """Declare the tenant's finding adapter."""
    return registry.service(FindingStore, lifetime=Lifetime.TENANT)


def register_findings_absent(registry: Registry) -> None:
    """Declare the tenant's finding adapter absent, for the store-free graph."""
    registry.absent(port(FindingStore), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
