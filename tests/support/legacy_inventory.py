"""The legacy leaves and bags the hub's registry may still declare, each named with the area that owns it.

A hub service is born in its area's wiring; a legacy leaf or bag is transitional, so the registry may declare only
those listed here, and an entry whose registration is gone is stale. Adopting a value removes its entry."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.registry.internal.recording import Kind, RecordingRegistry
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import HubTransactions

#: Each legacy key the hub registry declares, and the area that owns it until it adopts it.
LEGACY_INVENTORY: Mapping[type, str] = {
    HubScopedReads: "hub identity",
    OpenTenants: "hub identity",
    MembershipRepository: "hub identity",
    ScopedConnections: "hub scoped store adapters",
    HubTransactions: "hub scoped store adapters",
    ProjectStore: "projects",
}


def inventory_violations(registry: RecordingRegistry, allowed: Mapping[type, str] = LEGACY_INVENTORY) -> list[str]:
    """Each leaf or bag ``registry`` declares that ``allowed`` does not list, and each listed key it no longer
    declares."""
    declared = {entry.key: entry.kind for entry in registry.entries if entry.kind in (Kind.LEAF, Kind.BAG)}
    problems = [
        f"{getattr(key, '__qualname__', key)} is an unlisted legacy {kind.value}"
        for key, kind in declared.items()
        if key not in allowed
    ]
    problems.extend(
        f"{key.__qualname__} ({area}) is listed but no longer declared"
        for key, area in allowed.items()
        if key not in declared
    )
    return problems
