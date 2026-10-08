"""``HubCore.open(scope)`` hands out one tenant's stores, built once per tenant and refused for a
tenant that does not exist or has closed; ``HubScopedReads`` marks every statement it issues as
hub-scoped (component tier, over a migrated store)."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, event

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import HubCore, build_hub_core
from blizzard.hub.store import schema
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantClosed, TenantId, UnknownTenant
from tests.support import default_tenant, migrate_to

pytestmark = pytest.mark.component

_AT = datetime(2026, 10, 8, tzinfo=UTC)
_SECOND = TenantId("ten_SECOND")


def _core(tmp_path: Path) -> tuple[HubCore, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    return build_hub_core(engine, clock=FixedClock(_AT)), engine


def _add_tenant(engine: Engine, tenant: TenantId) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(schema.tenants).values(tenant_id=tenant, name=tenant, created_at=_AT, created_by="test"))


def _close_tenant(engine: Engine, tenant: TenantId) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(schema.tenant_deletions).values(tenant_id=tenant, name=tenant, deleted_by="test"))


def test_a_tenants_stores_are_built_once(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    scope = StoreScope(default_tenant(engine))

    assert core.open(scope) is core.open(scope)


def test_a_single_tenant_hub_opens_the_stores_it_already_serves(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)

    stores = core.open(StoreScope(default_tenant(engine)))

    assert stores is core.sole_tenant
    assert stores.cycle_lock is core.sole_tenant.cycle_lock


def test_each_tenant_opens_its_own_stores_over_its_own_scope(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    first = default_tenant(engine)
    _add_tenant(engine, _SECOND)

    mine = core.open(StoreScope(first))
    theirs = core.open(StoreScope(_SECOND))

    assert mine.scope == StoreScope(first)
    assert theirs.scope == StoreScope(_SECOND)
    assert theirs.registry is not mine.registry
    assert theirs.cycle_lock is not mine.cycle_lock
    assert theirs.chunk_stores.record is not mine.chunk_stores.record


def test_an_unknown_tenant_is_refused(tmp_path: Path) -> None:
    core, _ = _core(tmp_path)

    with pytest.raises(UnknownTenant):
        core.open(StoreScope(TenantId("ten_NOBODY")))


def test_a_closed_tenant_is_refused_even_once_its_stores_are_cached(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    core.open(StoreScope(_SECOND))

    _close_tenant(engine, _SECOND)

    with pytest.raises(TenantClosed):
        core.open(StoreScope(_SECOND))


def test_an_evicted_tenants_next_open_builds_its_stores_afresh(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    before = core.open(StoreScope(_SECOND))

    core.tenants.evict(_SECOND)

    after = core.open(StoreScope(_SECOND))
    assert after is not before
    assert after.scope == StoreScope(_SECOND)


def test_racing_opens_of_one_tenant_share_one_set_of_stores(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    start = threading.Barrier(8)

    def open_second(_: int) -> int:
        start.wait()
        return id(core.open(StoreScope(_SECOND)))

    with ThreadPoolExecutor(max_workers=8) as pool:
        opened = set(pool.map(open_second, range(8)))

    assert len(opened) == 1


def test_hub_scoped_reads_mark_every_statement_they_issue(tmp_path: Path) -> None:
    migrate_to(tmp_path, "head")
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    core = build_hub_core(engine, clock=FixedClock(_AT))
    tenant = default_tenant(engine)
    seen: list[Mapping[str, Any]] = []

    def record(
        conn: sa.Connection,
        clauseelement: Any,
        multiparams: Any,
        params: Any,
        execution_options: Mapping[str, Any],
    ) -> None:
        seen.append(execution_options)

    event.listen(engine, "before_execute", record)
    core.scoped_reads.tenants.confirm_open(tenant)

    assert seen
    assert all(options.get(HUB_SCOPED_OPTION) for options in seen)
