"""Helpers for the sweeps per tenant tests, shared with other test modules."""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard.hub.domain.chunk.model import Chunk, CloseEnqueueDecision, WorkRef
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.transactions import HubTransactions
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support import (
    HubHarness,
    hub_level_connections,
    make_graph,
    seed_chunk_record,
)

_NOW = datetime(2026, 7, 13, tzinfo=UTC)


def _stores_of(hub: HubHarness, tenant: TenantId) -> ChunkStores:
    connections = hub_level_connections(hub.engine).scoped(StoreScope(tenant))
    return build_chunk_stores(
        connections, hub.clock, registry=RunnerRegistryStore(connections), transactions=HubTransactions(connections)
    )


def landed_hub_ref(hub: HubHarness, tenant: TenantId, chunk_id: str) -> ChunkStores:
    """A chunk of ``tenant`` holding the hub source's ref ``1`` — the same ref in every tenant — landed,
    so its close intent is pending; the tenant's chunk stores."""
    connections = hub_level_connections(hub.engine).scoped(StoreScope(tenant))
    graph_id = f"gr_{chunk_id}"
    GraphStore(connections).mint(make_graph(graph_id, "g"), definition_yaml="", at=hub.clock.now())
    stores = _stores_of(hub, tenant)
    seed_chunk_record(
        stores, Chunk(chunk_id=chunk_id, graph_id=graph_id, work_refs=[WorkRef("hub", "1")], minted_at=_NOW)
    )
    stores.artifacts.record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name="merged/widget",
        content="sha",
        at=hub.clock.now(),
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=CloseEnqueueDecision(closable=frozenset({WorkRef("hub", "1")}), skipped=frozenset()),
    )
    return stores


def close_events(hub: HubHarness, tenant: TenantId) -> list[str]:
    connections = hub_level_connections(hub.engine).scoped(StoreScope(tenant))
    log = s.event_log
    with connections.read("test close events") as conn:
        rows = conn.execute(connections.select(log).where(log.c.kind.like("work-item-close%"))).all()
    return [row.chunk_id for row in rows]
