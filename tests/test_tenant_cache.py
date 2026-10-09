"""``HubCore.open(scope)`` hands out one tenant's stores — and, once the hub serves tenants, its
services — built once per tenant, answered from memory once cached, refused on a first open of a
tenant that does not exist or has closed, and dropped by eviction; ``HubScopedReads`` marks every
statement it issues as hub-scoped (component tier, over a migrated store)."""

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
from blizzard.hub.composition import (
    HubCore,
    HubServices,
    LiveConfig,
    TenantCache,
    TenantStores,
    build_hub_core,
    build_services,
    build_tenant_stores,
)
from blizzard.hub.events.brokers import TenantBrokers
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store import schema
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantClosed, TenantId, UnknownTenant
from blizzard.hub.work_sources.source import IWorkSourceRegistry
from tests.support import WorkSourceRegistry, add_tenant, close_tenant, count_queries, default_tenant, migrate_to

pytestmark = pytest.mark.component

_AT = datetime(2026, 10, 8, tzinfo=UTC)
_SECOND = TenantId("ten_SECOND")


def _core(tmp_path: Path) -> tuple[HubCore, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    return build_hub_core(engine, clock=FixedClock(_AT)), engine


def _add_tenant(engine: Engine, tenant: TenantId) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(schema.tenants).values(tenant_id=tenant, name=tenant, created_at=_AT, created_by="test"))


def _reopen_tenant(engine: Engine, tenant: TenantId) -> None:
    with engine.begin() as conn:
        conn.execute(sa.delete(schema.tenant_deletions).where(schema.tenant_deletions.c.tenant_id == tenant))


def _finish_deleting(engine: Engine, tenant: TenantId) -> None:
    with engine.begin() as conn:
        conn.execute(sa.delete(schema.tenants).where(schema.tenants.c.tenant_id == tenant))


def test_a_tenants_stores_are_built_once(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    scope = StoreScope(default_tenant(engine))

    assert core.open(scope) is core.open(scope)


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


def test_a_cached_tenant_is_answered_from_memory_with_no_statement(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    core.open(StoreScope(_SECOND))

    assert count_queries(engine, lambda: core.open(StoreScope(_SECOND))) == 0


def test_a_closed_tenant_is_refused_once_evicted_and_its_cached_stores_dropped(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    cached = core.open(StoreScope(_SECOND))

    close_tenant(engine, _SECOND)
    core.tenants.evict(_SECOND)

    with pytest.raises(TenantClosed):
        core.open(StoreScope(_SECOND))
    _reopen_tenant(engine, _SECOND)
    assert core.open(StoreScope(_SECOND)) is not cached


def test_a_tenant_whose_deletion_has_finished_is_refused_as_closed(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    close_tenant(engine, _SECOND)

    _finish_deleting(engine, _SECOND)

    with pytest.raises(TenantClosed):
        core.open(StoreScope(_SECOND))


def test_a_tenant_closed_while_its_stores_are_being_opened_is_dropped_by_its_eviction(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    confirm_open = core.hub_scoped_reads.tenants.confirm_open
    built: list[TenantStores] = []
    evicting: list[threading.Thread] = []

    def build(scope: StoreScope) -> TenantStores:
        built.append(build_tenant_stores(core.store_connections.scoped(scope), clock=FixedClock(_AT)))
        return built[-1]

    def confirm_then_close(tenant: TenantId) -> None:
        # The tenant's deletion commits once the open's check has passed, then evicts — waiting on the
        # cache's lock, which the open holds until its entry is cached.
        confirm_open(tenant)
        if not evicting:
            close_tenant(engine, tenant)
            evicting.append(threading.Thread(target=cache.evict, args=(tenant,)))
            evicting[0].start()

    cache = TenantCache(build=build, confirm_open=confirm_then_close)

    handed = cache.open(StoreScope(_SECOND))
    evicting[0].join(timeout=10.0)

    with pytest.raises(TenantClosed):
        cache.open(StoreScope(_SECOND))
    _reopen_tenant(engine, _SECOND)
    reopened = cache.open(StoreScope(_SECOND))
    assert handed is built[0]
    assert reopened is built[-1]
    assert reopened is not handed


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
    core.hub_scoped_reads.tenants.confirm_open(tenant)

    assert seen
    assert all(options.get(HUB_SCOPED_OPTION) for options in seen)


def _served(core: HubCore, tmp_path: Path, brokers: TenantBrokers) -> HubServices:
    def work_sources(_stores: TenantStores, _live: LiveConfig) -> IWorkSourceRegistry:
        return WorkSourceRegistry({})

    return build_services(
        core, work_sources=work_sources, secret_keys=hub_key_provider({}, data_dir=tmp_path), brokers=brokers
    )


def test_a_tenants_services_are_built_once_over_its_own_stores(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    hub = _served(core, tmp_path, TenantBrokers())
    first = hub.tenants.open_services(StoreScope(default_tenant(engine)))
    second = add_tenant(engine, "second")

    services = hub.tenants.open_services(StoreScope(second))

    assert hub.tenants.open_services(StoreScope(second)) is services
    stores = core.open(StoreScope(second))
    assert services.registry is stores.registry
    assert services.delete is stores.delete
    assert services.finding_exit is stores.finding_exit
    assert services is not first
    assert services.registry is not first.registry


def test_each_tenant_publishes_to_its_own_broker(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    brokers = TenantBrokers()
    hub = _served(core, tmp_path, brokers)
    tenant = default_tenant(engine)
    first = hub.tenants.open_services(StoreScope(tenant))
    second = add_tenant(engine, "second")

    services = hub.tenants.open_services(StoreScope(second))

    assert first.events is brokers.for_tenant(tenant)
    assert services.events is brokers.for_tenant(second)
    assert services.events is not first.events


def test_eviction_drops_a_tenants_services_and_its_broker(tmp_path: Path) -> None:
    core, engine = _core(tmp_path)
    brokers = TenantBrokers()
    hub = _served(core, tmp_path, brokers)
    second = add_tenant(engine, "second")
    before = hub.tenants.open_services(StoreScope(second))
    broker = before.events

    core.tenants.evict(second)

    after = hub.tenants.open_services(StoreScope(second))
    assert after is not before
    assert after.registry is not before.registry
    assert brokers.for_tenant(second) is not broker
    assert after.events is brokers.for_tenant(second)
