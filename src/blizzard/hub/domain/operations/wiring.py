"""The chunk operations area's registrations: promote, demote, pause, stop, restart, complete, edit, delete, queue
and group, and the hub-item withdrawals a delete reads and writes.

The services resolve the chunk ports and the event log by key; the area names no adapter. Restart and complete judge a
source's closer against the tenant's configured work sources. The store-free graph declares each registration absent,
from the same roster."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.flow_submissions import IReadChunkFlowSubmissions
from blizzard.hub.domain.chunk.ports.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.work_sources import IWorkClosers
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.operations.complete import CompleteService
from blizzard.hub.domain.operations.delete import DeleteService, IHubItemWithdrawals
from blizzard.hub.domain.operations.demote import DemoteService
from blizzard.hub.domain.operations.edit import EditService
from blizzard.hub.domain.operations.pause import PauseService
from blizzard.hub.domain.operations.promote import PromoteService
from blizzard.hub.domain.operations.queue import GroupService, QueueService
from blizzard.hub.domain.operations.restart import RestartService
from blizzard.hub.domain.operations.stop import StopService
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def restart_service(
    movement: IWriteChunkMovementRepository,
    submissions: IReadChunkFlowSubmissions,
    decisions: IWriteChunkDecisionsRepository,
    facts: IReadChunkFactsRepository,
    graphs: IReadGraphRepository,
    clock: IClock,
    transactions: IHubTransactions,
    record: IReadChunkRecordRepository,
    closers: IWorkClosers,
    events: EventLogService,
) -> RestartService:
    """The tenant's restart, its source closers judged against the tenant's configured work sources."""
    return RestartService(
        movement=movement,
        submissions=submissions,
        decisions=decisions,
        facts=facts,
        graphs=graphs,
        clock=clock,
        transactions=transactions,
        record=record,
        closer_available=lambda source: closers.closer(source) is not None,
        events=events,
    )


def complete_service(
    lifecycle: IWriteChunkLifecycleRepository,
    submissions: IReadChunkFlowSubmissions,
    facts: IReadChunkFactsRepository,
    record: IReadChunkRecordRepository,
    transactions: IHubTransactions,
    clock: IClock,
    closers: IWorkClosers,
    events: EventLogService,
) -> CompleteService:
    """The tenant's manual completion, its source closers judged against the tenant's configured work sources."""
    return CompleteService(
        lifecycle=lifecycle,
        submissions=submissions,
        facts=facts,
        record=record,
        transactions=transactions,
        clock=clock,
        closer_available=lambda source: closers.closer(source) is not None,
        events=events,
    )


#: The area's services registered by class.
_SERVICES: tuple[type[Any], ...] = (
    PromoteService,
    DemoteService,
    PauseService,
    StopService,
    EditService,
    DeleteService,
    QueueService,
    GroupService,
)


def register(registry: Registry, *, withdrawals: Ref[IHubItemWithdrawals]) -> None:
    """Declare the operations once per tenant, and the hub-item withdrawals over the adapter handed in."""
    registry.alias(port(IHubItemWithdrawals), withdrawals)
    for service in _SERVICES:
        registry.service(service, lifetime=Lifetime.TENANT)
    registry.factory(restart_service, lifetime=Lifetime.TENANT)
    registry.factory(complete_service, lifetime=Lifetime.TENANT)


#: Every tenant key :func:`register` declares, for the store-free graph to declare absent.
_TENANT_KEYS: tuple[type[Any], ...] = (IHubItemWithdrawals, *_SERVICES, RestartService, CompleteService)


def register_absent(registry: Registry) -> None:
    """Declare the area's services and the withdrawals' port absent, for the store-free graph."""
    for missing in _TENANT_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
