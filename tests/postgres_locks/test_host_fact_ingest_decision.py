"""The actual host ingest decision waits on the registry before reading its mark."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.fact_kinds import EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED
from blizzard.hub.domain.execution.facts import PushedFact
from blizzard.hub.domain.execution.host_facts import HostFactIngestService
from blizzard.hub.domain.runners.host import HostAddition
from blizzard.hub.store import schema
from blizzard.hub.store.internal import host_registry_store
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("omit_registry", [False, True], ids=["registry-lock", "omission-control"])
def test_ingest_decision_waits_on_direct_registry_key(database_url, monkeypatch, omit_registry):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "host_fact_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "host_fact_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_host_fact_decision")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="host facts", created_at=now, created_by="test"):
            pass
        hosts = HostRegistryStore(scoped)
        hosts.add(HostAddition("host", "host", "hash", now, "test"))
        host = hosts.get_host("host")
        assert host is not None
        waiting = HostRegistryStore(scoped_connections(waiter, tenant))
        service = HostFactIngestService(hosts=waiting, clock=SystemClock())
        fact = PushedFact(1, EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED, {"slug": "test", "name": "Test", "windows": []})
        if omit_registry:
            monkeypatch.setattr(host_registry_store, "lock_host_registry", lambda conn: None)

        with ThreadPoolExecutor(max_workers=1) as pool:
            # read() deliberately avoids tenant admission; this transaction owns only the decision key.
            with scoped.read("hold_host_fact_registry") as conn:
                lock_keys(conn, "host-live-name-registry", ["registry"])
                future = pool.submit(service.ingest, host, [fact])
                deadline = monotonic() + 5
                with observer.connect().execution_options(isolation_level="AUTOCOMMIT") as watch:
                    while monotonic() < deadline:
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                              JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                              WHERE w.application_name = 'host_fact_waiter'
                                AND h.application_name = 'host_fact_holder'
                                AND w.query ILIKE '%keyed_locks%')
                        """)
                        ).scalar_one()
                        if blocked or future.done():
                            break
                        Event().wait(0.02)
                    else:
                        pytest.fail("ingest neither blocked on the key nor completed")
                assert blocked is not omit_registry
                if omit_registry:
                    assert future.result(timeout=5).applied == [1]
            result = future.result(timeout=10)
        assert result.applied == [1]
        assert waiting.high_water("host") == 1
        replay = service.ingest(host, [fact])
        assert replay.already_applied == [1] and replay.applied == []
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


def test_ingest_reads_high_water_after_registry_holder_commits(database_url):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "host_fact_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "host_fact_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_host_fact_recheck")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="host facts", created_at=now, created_by="test"):
            pass
        hosts = HostRegistryStore(scoped)
        hosts.add(HostAddition("host", "host", "hash", now, "test"))
        host = hosts.get_host("host")
        assert host is not None
        waiting = HostRegistryStore(scoped_connections(waiter, tenant))
        service = HostFactIngestService(hosts=waiting, clock=SystemClock())
        fact = PushedFact(1, EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED, {"slug": "test", "name": "Test", "windows": []})
        with ThreadPoolExecutor(max_workers=1) as pool:
            with scoped.read("advance_under_registry") as conn:
                lock_keys(conn, "host-live-name-registry", ["registry"])
                conn.execute(scoped.insert(schema.host_high_water).values(host_id="host", seq=2, updated_at=now))
                future = pool.submit(service.ingest, host, [fact])
                deadline = monotonic() + 5
                with observer.connect().execution_options(isolation_level="AUTOCOMMIT") as watch:
                    while monotonic() < deadline:
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                              JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                              WHERE w.application_name = 'host_fact_waiter'
                                AND h.application_name = 'host_fact_holder'
                                AND w.query ILIKE '%keyed_locks%')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not future.done(), "ingest completed before the mark committed"
                        Event().wait(0.02)
                    else:
                        pytest.fail("ingest did not wait on the registry")
                conn.commit()
            result = future.result(timeout=10)
        assert result.high_water == 2
        assert result.applied == [] and result.already_applied == [1]
        assert waiting.usage_of(["host"])["host"].samples == ()
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
