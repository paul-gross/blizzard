"""The chunk operations registrations as the hub container holds them: within a tenant each chunk port, work-item port,
operation and retained bag field is one instance, the operations hold the container's chunk ports, event log and
delete, and the event log publishes on its tenant's broker; across tenants the instances differ (component tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.domain.chunk.dependencies import DependencyService
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.ingest import IngestService, IWorkItemProjectRead
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.flow_submissions import IReadChunkFlowSubmissions
from blizzard.hub.domain.chunk.ports.lifecycle import IReadChunkLifecycleRepository, IWriteChunkLifecycleRepository
from blizzard.hub.domain.chunk.ports.queue import IReadChunkQueueRepository, IWriteChunkQueueRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository, IWriteChunkRecordRepository
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.operations.complete import CompleteService
from blizzard.hub.domain.operations.delete import DeleteService, IHubItemWithdrawals
from blizzard.hub.domain.operations.demote import DemoteService
from blizzard.hub.domain.operations.edit import EditService
from blizzard.hub.domain.operations.pause import PauseService
from blizzard.hub.domain.operations.promote import PromoteService
from blizzard.hub.domain.operations.queue import GroupService, QueueService
from blizzard.hub.domain.operations.restart import RestartService
from blizzard.hub.domain.operations.stop import StopService
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.domain.work_items.ports import IReadWorkItemRepository, IWriteWorkItemRepository
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.tenancy import StoreScope
from tests.support.scaffolding import HubHarness, add_tenant, build_hub, default_tenant


def _shared(hub: HubHarness, tenant: StoreScope) -> dict[str, list[object]]:
    """Every holder of each chunk operations instance in ``tenant``, by what the instance is."""
    scope = hub.process.tenants.open(tenant).resolver
    services = hub.hub_services.tenants.open_services(tenant)
    chunks = scope.get(ChunkStores)
    # The services hold what they were built over privately; reading it is the only way to see it is shared.
    return {
        "chunk facts": [
            chunks.facts,
            scope.get(IReadChunkFactsRepository),
            services.chunks.facts,
            scope.get(PromoteService)._facts,  # pyright: ignore[reportPrivateUsage]
        ],
        "chunk record": [
            chunks.record,
            scope.get(IReadChunkRecordRepository),
            scope.get(IWriteChunkRecordRepository),
            services.chunks.record,
            scope.get(IngestService)._record,  # pyright: ignore[reportPrivateUsage]
        ],
        "chunk lifecycle": [
            chunks.lifecycle,
            scope.get(IReadChunkLifecycleRepository),
            scope.get(IWriteChunkLifecycleRepository),
            scope.get(PauseService)._lifecycle,  # pyright: ignore[reportPrivateUsage]
        ],
        "chunk queue": [
            chunks.queue,
            scope.get(IReadChunkQueueRepository),
            scope.get(IWriteChunkQueueRepository),
            scope.get(QueueService)._queue,  # pyright: ignore[reportPrivateUsage]
        ],
        "work items": [
            scope.get(WorkItemStore),
            scope.get(IReadWorkItemRepository),
            scope.get(IWriteWorkItemRepository),
            scope.get(IHubItemWithdrawals),
            scope.get(IWorkItemProjectRead),
        ],
        "flow submissions": [scope.get(FlowStore), scope.get(IReadChunkFlowSubmissions)],
        "event log": [
            scope.get(EventLogService),
            services.event_log,
            scope.get(RestartService)._events,  # pyright: ignore[reportPrivateUsage]
            scope.get(CompleteService)._events,  # pyright: ignore[reportPrivateUsage]
        ],
        "delete": [
            scope.get(DeleteService),
            scope.get(WorkItemEditService)._delete,  # pyright: ignore[reportPrivateUsage]
        ],
        "ingest": [scope.get(IngestService)],
        "promote": [scope.get(PromoteService)],
        "demote": [scope.get(DemoteService)],
        "restart": [scope.get(RestartService)],
        "pause": [scope.get(PauseService)],
        "stop": [scope.get(StopService)],
        "complete": [scope.get(CompleteService)],
        "edit": [scope.get(EditService)],
        "dependencies": [scope.get(DependencyService)],
        "queue": [scope.get(QueueService)],
        "group": [scope.get(GroupService)],
        "work item edits": [scope.get(WorkItemEditService)],
    }


def test_the_chunk_operations_keys_share_identity_within_a_tenant_and_differ_across_tenants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = StoreScope(default_tenant(hub.engine))
    second = StoreScope(add_tenant(hub.engine, "second"))
    hub.hub_services.tenants.open_tenants.refresh()

    mine, theirs = _shared(hub, first), _shared(hub, second)

    for name, held in (*mine.items(), *theirs.items()):
        assert all(each is held[0] for each in held), name
    for (name, held), (_, other) in zip(mine.items(), theirs.items(), strict=True):
        assert held[0] is not other[0], name
    for tenant in (first, second):
        scope = hub.process.tenants.open(tenant).resolver
        # The event log publishes live on its own tenant's broker.
        assert scope.get(EventLogService)._publisher is scope.get(EventBroker)  # pyright: ignore[reportPrivateUsage]
