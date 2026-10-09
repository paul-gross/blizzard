"""The hub-execution slot is one per tenant: each tenant's hub-executed nodes run one at a time,
and a tenant's live slot — fresh or stale — never holds, nor is reclaimed by, another tenant."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert

from blizzard.foundation.clock import FixedClock
from blizzard.hub.store import schema as hub
from blizzard.hub.store.internal.chunk_hub_exec_store import ChunkHubExecStore
from blizzard.hub.tenancy import TenantId
from tests.support import add_tenant, default_tenant, migrate_to, scoped_connections, stamp

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 14, tzinfo=UTC)
_STALE_AFTER = timedelta(minutes=5)


def _two_tenants(tmp_path: Path) -> tuple[Engine, TenantId, TenantId]:
    _, engine = migrate_to(tmp_path, "head")
    first = default_tenant(engine)
    second = add_tenant(engine, "second")
    return engine, first, second


def _chunk(engine: Engine, tenant: TenantId, chunk_id: str) -> str:
    with engine.begin() as conn:
        conn.execute(
            insert(hub.chunks).values(
                stamp(hub.chunks, chunk_id=chunk_id, graph_id="gr_1", minted_at=_NOW, model="m", tenant_id=tenant)
            )
        )
    return chunk_id


def _slots(engine: Engine, tenant: TenantId) -> ChunkHubExecStore:
    return ChunkHubExecStore(scoped_connections(engine, tenant), FixedClock(_NOW))


def test_two_tenants_each_hold_a_live_slot_at_once(tmp_path: Path) -> None:
    engine, first, second = _two_tenants(tmp_path)
    first_slots, second_slots = _slots(engine, first), _slots(engine, second)

    held = first_slots.acquire_hub_exec_slot(
        _chunk(engine, first, "ch_a1"), node_id="nd_merge", at=_NOW, stale_after=_STALE_AFTER
    )
    other = second_slots.acquire_hub_exec_slot(
        _chunk(engine, second, "ch_b1"), node_id="nd_merge", at=_NOW, stale_after=_STALE_AFTER
    )

    assert held is not None
    assert other is not None
    assert first_slots.count_live_hub_exec_slots() == 1
    assert second_slots.count_live_hub_exec_slots() == 1


def test_a_tenant_still_holds_at_most_one_live_slot(tmp_path: Path) -> None:
    engine, first, second = _two_tenants(tmp_path)
    first_slots = _slots(engine, first)
    assert _slots(engine, second).acquire_hub_exec_slot(
        _chunk(engine, second, "ch_b1"), node_id="nd_merge", at=_NOW, stale_after=_STALE_AFTER
    )
    assert first_slots.acquire_hub_exec_slot(
        _chunk(engine, first, "ch_a1"), node_id="nd_merge", at=_NOW, stale_after=_STALE_AFTER
    )

    deferred = first_slots.acquire_hub_exec_slot(
        _chunk(engine, first, "ch_a2"), node_id="nd_merge", at=_NOW + timedelta(seconds=1), stale_after=_STALE_AFTER
    )

    assert deferred is None
    assert first_slots.count_live_hub_exec_slots() == 1


def test_a_stale_slot_is_reclaimed_only_by_its_own_tenant(tmp_path: Path) -> None:
    engine, first, second = _two_tenants(tmp_path)
    first_slots, second_slots = _slots(engine, first), _slots(engine, second)
    assert first_slots.acquire_hub_exec_slot(
        _chunk(engine, first, "ch_a1"), node_id="nd_merge", at=_NOW, stale_after=_STALE_AFTER
    )
    later = _NOW + _STALE_AFTER * 2

    assert second_slots.acquire_hub_exec_slot(
        _chunk(engine, second, "ch_b1"), node_id="nd_merge", at=later, stale_after=_STALE_AFTER
    )
    assert first_slots.count_live_hub_exec_slots() == 1

    assert first_slots.acquire_hub_exec_slot(
        _chunk(engine, first, "ch_a2"), node_id="nd_merge", at=later, stale_after=_STALE_AFTER
    )
    assert first_slots.count_live_hub_exec_slots() == 1
    assert second_slots.count_live_hub_exec_slots() == 1


def test_the_boot_release_frees_only_its_own_tenants_slots(tmp_path: Path) -> None:
    engine, first, second = _two_tenants(tmp_path)
    first_slots, second_slots = _slots(engine, first), _slots(engine, second)
    for slots, tenant, chunk_id in ((first_slots, first, "ch_a1"), (second_slots, second, "ch_b1")):
        assert slots.acquire_hub_exec_slot(
            _chunk(engine, tenant, chunk_id), node_id="nd_merge", at=_NOW, stale_after=_STALE_AFTER
        )

    assert first_slots.release_live_hub_exec_slots(at=_NOW) == 1

    assert first_slots.count_live_hub_exec_slots() == 0
    assert second_slots.count_live_hub_exec_slots() == 1
