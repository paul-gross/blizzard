"""The legacy leaves and bags the hub's registry may still declare, each named with the area that owns it.

A hub service is born in its area's wiring; a legacy leaf or bag is transitional, so the registry may declare only
those listed here, and an entry whose registration is gone is stale. Adopting a value removes its entry."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.registry.internal.recording import Kind, RecordingRegistry
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.internal.identity_transaction import IdentityTransactions
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.delivery.flow_driver import StepWake
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.garden.proposals.resolution import GardenProposalDeliveryResolution
from blizzard.hub.domain.graph.authoring import DefaultGraphService, GraphMintService
from blizzard.hub.domain.operations.delete import DeleteService
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.domain.urgency.services import TenantDefault, UrgencyReading
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.open_tenants import OpenTenants

#: Each legacy key the hub registry declares (a class or a callable alias), and the area that owns it until adopted.
LEGACY_INVENTORY: Mapping[object, str] = {
    RepoErrorFactory: "hub identity",
    IdentityTransactions: "hub identity",
    OpenTenants: "hub identity",
    MembershipRepository: "hub identity",
    UserRepository: "hub identity",
    ActionLoop: "hub lifecycle",
    StepWake: "hub lifecycle",
    ColorPick: "projects",
    DeleteService: "chunk operations",
    WorkItemEditService: "chunk operations",
    EventLogService: "chunk operations",
    IReadChunkFactsRepository: "chunk operations",
    IReadChunkRecordRepository: "chunk operations",
    IReadChunkRouteRepository: "chunk operations",
    IReadChunkWorkRefsRepository: "chunk operations",
    TenantDefault: "projects and urgency",
    UrgencyReading: "projects and urgency",
    GraphMintService: "graphs",
    DefaultGraphService: "graphs",
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
