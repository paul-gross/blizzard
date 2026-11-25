"""The work-items area's registrations: the hub work-item repository's ports over the adapter the root hands in, the
hub work-item edits over them, and the close drain.

The close drain's tenant half is built once per tenant over that tenant's delivery, artifacts, chunk records, event
log, work closers and landed-commit links, linking each chunk's board page under the hub's public URL, a key the config
edge supplies. Its pass is one per process, reaching each tenant's half through the :class:`CloseIntentDrainers`
accessor the root supplies and binds over its tenant scopes.

The area names no adapter. The store-free graph declares each registration absent, from the same rosters."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.commit_links import IReadLandedCommitLinks
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.work_sources import IWorkClosers
from blizzard.hub.domain.work_items.closure import (
    BoardPublicUrl,
    CloseDrainSweep,
    CloseIntentDrainer,
    CloseIntentDrainers,
    IReadCloseIntentCorpus,
)
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.domain.work_items.ports import IReadWorkItemRepository, IWriteWorkItemRepository
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def close_intent_drainer(
    delivery: IWriteChunkDeliveryRepository,
    artifacts: IReadChunkArtifactsRepository,
    chunks: IReadChunkRecordRepository,
    events: EventLogService,
    work_sources: IWorkClosers,
    clock: IClock,
    commit_links: IReadLandedCommitLinks,
    public_url: BoardPublicUrl,
) -> CloseIntentDrainer:
    """The tenant's half of the close drain, linking each chunk's board page under the hub's public URL."""
    return CloseIntentDrainer(
        delivery=delivery,
        artifacts=artifacts,
        chunks=chunks,
        events=events,
        work_sources=work_sources,
        clock=clock,
        commit_links=commit_links,
        public_url=public_url.url,
    )


def close_drain_sweep(corpus: IReadCloseIntentCorpus, drainers: CloseIntentDrainers, clock: IClock) -> CloseDrainSweep:
    """The close drain's one pass per hub, handing each tenant its share through its own drainer."""
    return CloseDrainSweep(corpus=corpus, tenant=drainers, clock=clock)


def register(registry: Registry, *, items: Ref[IWriteWorkItemRepository]) -> Ref[CloseDrainSweep]:
    """Declare the work-item read and write ports over the adapter handed in, the edits and the close drain's tenant
    half, once per tenant; and the close drain's pass and the accessor it reaches each tenant through, once per
    process, handing back the pass's ``Ref``."""
    registry.alias(port(IReadWorkItemRepository), items)
    registry.alias(port(IWriteWorkItemRepository), items)
    registry.service(WorkItemEditService, lifetime=Lifetime.TENANT)
    registry.factory(close_intent_drainer, lifetime=Lifetime.TENANT)
    registry.context(port(CloseIntentDrainers), lifetime=Lifetime.PROCESS)
    return registry.factory(close_drain_sweep, lifetime=Lifetime.PROCESS)


#: Every tenant key :func:`register` declares, for the store-free graph to declare absent.
_TENANT_KEYS: tuple[type[Any], ...] = (
    IReadWorkItemRepository,
    IWriteWorkItemRepository,
    WorkItemEditService,
    CloseIntentDrainer,
)

#: Every process key :func:`register` declares.
_PROCESS_KEYS: tuple[type[Any], ...] = (CloseIntentDrainers, CloseDrainSweep)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports, the edits and the close drain absent, for the store-free graph. The public URL is
    declared by its provider."""
    for missing in _TENANT_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    for missing in _PROCESS_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
