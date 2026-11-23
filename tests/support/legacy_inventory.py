"""The legacy leaves and bags the hub's registry may still declare, each named with the area that owns it.

A hub service is born in its area's wiring; a legacy leaf or bag is transitional, so the registry may declare only
those listed here, and an entry whose registration is gone is stale. Adopting a value removes its entry."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.registry.internal.recording import Kind, RecordingRegistry
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.delivery.flow_driver import StepWake
from blizzard.hub.domain.garden.proposals.resolution import GardenProposalDeliveryResolution
from blizzard.hub.open_tenants import OpenTenants

#: Each legacy key the hub registry declares (a class or a callable alias), and the area that owns it until adopted.
LEGACY_INVENTORY: Mapping[object, str] = {
    OpenTenants: "hub lifecycle",
    ActionLoop: "hub lifecycle",
    StepWake: "hub lifecycle",
    GardenProposalDeliveryResolution: "garden authoring",
}


def inventory_violations(registry: RecordingRegistry, allowed: Mapping[object, str] = LEGACY_INVENTORY) -> list[str]:
    """Each leaf or bag ``registry`` declares that ``allowed`` does not list, and each listed key it no longer
    declares."""
    declared = {entry.key: entry.kind for entry in registry.entries if entry.kind in (Kind.LEAF, Kind.BAG)}
    problems = [
        f"{getattr(key, '__qualname__', key)} is an unlisted legacy {kind.value}"
        for key, kind in declared.items()
        if key not in allowed
    ]
    problems.extend(
        f"{getattr(key, '__qualname__', key)} ({area}) is listed but no longer declared"
        for key, area in allowed.items()
        if key not in declared
    )
    return problems
