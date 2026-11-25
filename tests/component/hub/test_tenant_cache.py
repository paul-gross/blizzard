"""A hub process's tenant scopes hand out one tenant's stores and services, built once per tenant, answered from
memory once open, refused on a first open of a tenant that does not exist or has closed, and dropped by eviction;
``HubScopedReads`` marks every statement it issues as hub-scoped (component tier, over a migrated store)."""

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

from blizzard import __version__
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.api.version_window import HubVersionWindow
from blizzard.hub.composition import connections_for_engine
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry
from blizzard.hub.egress.factory import egress_boot
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.events.brokers import TenantBrokers
from blizzard.hub.process_root import HubProcess, open_store_backed
from blizzard.hub.store import schema
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantClosed, TenantId, UnknownTenant
from tests.support.config_inputs import VERSION_WINDOW
from tests.support.scaffolding import (
    TEST_HUB_ACTIONS,
    add_tenant,
    close_tenant,
    configured_core,
    count_queries,
    default_tenant,
    migrate_to,
)

_AT = datetime(2026, 10, 8, tzinfo=UTC)
_SECOND = TenantId("ten_SECOND")


def _core(tmp_path: Path) -> tuple[HubProcess, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    config = HubConfig(root=tmp_path, db_url=str(engine.url))
    core = open_store_backed(
        connections_for_engine(engine),
        clock=FixedClock(_AT),
        actions=TEST_HUB_ACTIONS,
        config=config,
        platform_tracing=DisabledPlatformTracing(),
        environ={},
        versions=HubVersionWindow(__version__, VERSION_WINDOW),
        egress_boot=egress_boot(config.egress, None),
    )
    return core, engine


def _add_tenant(engine: Engine, tenant: TenantId) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(schema.tenants).values(tenant_id=tenant, name=tenant, created_at=_AT, created_by="test"))


def _reopen_tenant(engine: Engine, tenant: TenantId) -> None:
    with engine.begin() as conn:
        conn.execute(sa.delete(schema.tenant_deletions).where(schema.tenant_deletions.c.tenant_id == tenant))


def _finish_deleting(engine: Engine, tenant: TenantId) -> None:
    with engine.begin() as conn:
        conn.execute(sa.delete(schema.tenants).where(schema.tenants.c.tenant_id == tenant))


def test_each_tenant_opens_its_own_stores_over_its_own_scope(tmp_path: Path) -> None:
    process, engine = _core(tmp_path)
    first = default_tenant(engine)
    _add_tenant(engine, _SECOND)

    mine = process.tenants.open(StoreScope(first)).resolver
    theirs = process.tenants.open(StoreScope(_SECOND)).resolver

    assert mine.get(ScopedConnections).scope == StoreScope(first)
    assert theirs.get(ScopedConnections).scope == StoreScope(_SECOND)
    assert theirs.get(IReadRunnerRegistry) is not mine.get(IReadRunnerRegistry)
    assert theirs.get(IReadChunkRecordRepository) is not mine.get(IReadChunkRecordRepository)


def test_an_unknown_tenant_is_refused(tmp_path: Path) -> None:
    process, _ = _core(tmp_path)

    with pytest.raises(UnknownTenant):
        process.tenants.open(StoreScope(TenantId("ten_NOBODY")))


def test_an_open_tenant_is_answered_from_memory_with_no_statement(tmp_path: Path) -> None:
    process, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    process.tenants.open(StoreScope(_SECOND))

    assert count_queries(engine, lambda: process.tenants.open(StoreScope(_SECOND))) == 0


def test_a_closed_tenant_is_refused_once_evicted_and_its_open_stores_dropped(tmp_path: Path) -> None:
    process, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    cached = process.tenants.resolve(StoreScope(_SECOND), IReadRunnerRegistry)

    close_tenant(engine, _SECOND)
    process.tenants.evict(_SECOND)

    with pytest.raises(TenantClosed):
        process.tenants.open(StoreScope(_SECOND))
    _reopen_tenant(engine, _SECOND)
    assert process.tenants.resolve(StoreScope(_SECOND), IReadRunnerRegistry) is not cached


def test_a_tenant_whose_deletion_has_finished_is_refused_as_closed(tmp_path: Path) -> None:
    process, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    close_tenant(engine, _SECOND)

    _finish_deleting(engine, _SECOND)

    with pytest.raises(TenantClosed):
        process.tenants.open(StoreScope(_SECOND))


def test_an_evicted_tenants_next_open_builds_its_stores_afresh(tmp_path: Path) -> None:
    process, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    before = process.tenants.resolve(StoreScope(_SECOND), ScopedConnections)

    process.tenants.evict(_SECOND)

    after = process.tenants.resolve(StoreScope(_SECOND), ScopedConnections)
    assert after is not before
    assert after.scope == StoreScope(_SECOND)


def test_racing_opens_of_one_tenant_share_one_set_of_stores(tmp_path: Path) -> None:
    process, engine = _core(tmp_path)
    _add_tenant(engine, _SECOND)
    start = threading.Barrier(8)

    def open_second(_: int) -> int:
        start.wait()
        return id(process.tenants.resolve(StoreScope(_SECOND), ScopedConnections))

    with ThreadPoolExecutor(max_workers=8) as pool:
        opened = set(pool.map(open_second, range(8)))

    assert len(opened) == 1


def test_hub_scoped_reads_mark_every_statement_they_issue(tmp_path: Path) -> None:
    migrate_to(tmp_path, "head")
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    process = configured_core(engine, tmp_path, clock=FixedClock(_AT))
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
    process.resolver.get(HubScopedReads).tenants.confirm_open(tenant)

    assert seen
    assert all(options.get(HUB_SCOPED_OPTION) for options in seen)


def test_each_tenant_publishes_to_its_own_broker(tmp_path: Path) -> None:
    process, engine = _core(tmp_path)
    brokers = process.resolver.get(TenantBrokers)
    tenant = default_tenant(engine)
    first = process.tenants.resolve(StoreScope(tenant), EventBroker)
    second = add_tenant(engine, "second")

    theirs = process.tenants.resolve(StoreScope(second), EventBroker)

    assert first is brokers.for_tenant(tenant)
    assert theirs is brokers.for_tenant(second)
    assert theirs is not first


def test_eviction_drops_a_tenants_services_and_its_broker(tmp_path: Path) -> None:
    process, engine = _core(tmp_path)
    brokers = process.resolver.get(TenantBrokers)
    second = add_tenant(engine, "second")
    scope = StoreScope(second)
    before = process.tenants.resolve(scope, IReadRunnerRegistry)
    broker = process.tenants.resolve(scope, EventBroker)

    # A tenant's deletion evicts its container scope, which holds the adapters and the broker.
    process.tenants.evict(second)

    assert process.tenants.resolve(scope, IReadRunnerRegistry) is not before
    assert brokers.for_tenant(second) is not broker
    assert process.tenants.resolve(scope, EventBroker) is brokers.for_tenant(second)
