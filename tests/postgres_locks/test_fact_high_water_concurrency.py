"""Host and runner fact high-water decisions serialize on their own keys."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic
from typing import Protocol, cast

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import SystemClock
from blizzard.hub.store import schema
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal import chunk_route_store, host_registry_store
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 27, tzinfo=UTC)
_TENANT = TenantId("ten_fact_water")


class HighWaterWriter(Protocol):
    def __call__(self, key: str, /, *, seq: int, at: datetime) -> None: ...


@pytest.mark.parametrize("lane", ["host", "runner"])
@pytest.mark.parametrize("existing", [False, True], ids=["first-insert", "descending-update"])
@pytest.mark.parametrize("omit", [False, True], ids=["key-lock", "omission-control"])
def test_concurrent_high_water_decisions(
    database_url: str, lane: str, existing: bool, omit: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "water_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "water_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    holder_scoped = scoped_connections(holder, _TENANT)
    waiter_scoped = scoped_connections(waiter, _TENANT)
    table = schema.host_high_water if lane == "host" else schema.runner_high_water
    module = host_registry_store if lane == "host" else chunk_route_store

    def writer(scoped) -> HighWaterWriter:
        if lane == "host":
            return host_registry_store.HostRegistryStore(scoped).set_high_water
        return chunk_route_store.ChunkRouteStore(scoped, SystemClock()).set_runner_high_water

    held = Event()
    release = Event()

    @event.listens_for(holder, "after_cursor_execute")
    def pause_after_write(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT INTO " + table.name.upper(), "UPDATE " + table.name.upper())):
            held.set()
            if not release.wait(10):
                raise TimeoutError("holder was not released")

    try:
        with holder_scoped.founding("found", name="water", created_at=_AT, created_by="test"):
            pass
        if existing:
            writer(waiter_scoped)("key", seq=1, at=_AT)
        monkeypatch.setattr(holder_scoped, "_lock_open_tenant", lambda conn: None)
        monkeypatch.setattr(waiter_scoped, "_lock_open_tenant", lambda conn: None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(writer(holder_scoped), "key", seq=9, at=_AT + timedelta(hours=1))
            try:
                assert held.wait(5), "first writer did not reach its uncommitted write"
                if omit:
                    monkeypatch.setattr(module, "lock_keys", lambda *args: None)
                second = pool.submit(writer(waiter_scoped), "key", seq=3, at=_AT + timedelta(hours=2))
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        query = watch.execute(
                            sa.text("""
                            SELECT w.query FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'water_waiter' AND h.application_name = 'water_holder'
                        """)
                        ).scalar_one_or_none()
                        if query is not None:
                            break
                        assert not second.done(), "second writer completed before the first committed"
                        Event().wait(0.02)
                    else:
                        pytest.fail("second writer did not block")
                assert ("keyed_locks" in query.lower()) is (not omit)
            finally:
                release.set()
            first.result(timeout=10)
            if omit and not existing:
                with pytest.raises(HubStoreError) as failure:
                    second.result(timeout=10)
                assert isinstance(failure.value.__cause__, IntegrityError)
            else:
                second.result(timeout=10)
        with waiter_scoped.read("check_water") as conn:
            row = conn.execute(waiter_scoped.select(table, table.c.seq, table.c.updated_at)).one()
        assert row.seq == (3 if omit and existing else 9)
        assert row.updated_at == _AT + timedelta(hours=2 if omit and existing else 1)
    finally:
        release.set()
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("lane", ["host", "runner"])
@pytest.mark.parametrize("omit", [False, True], ids=["key-lock", "omission-control"])
def test_high_water_waits_on_direct_key_without_tenant_admission(
    database_url: str, lane: str, omit: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "direct_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "direct_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    scoped = scoped_connections(holder, _TENANT)
    other = scoped_connections(waiter, _TENANT)
    module = host_registry_store if lane == "host" else chunk_route_store
    write = cast(
        HighWaterWriter,
        (
            host_registry_store.HostRegistryStore(other).set_high_water
            if lane == "host"
            else chunk_route_store.ChunkRouteStore(other, SystemClock()).set_runner_high_water
        ),
    )
    try:
        with scoped.founding("found", name="water", created_at=_AT, created_by="test"):
            pass
        if omit:
            monkeypatch.setattr(module, "lock_keys", lambda *args: None)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with scoped.read("hold_key") as conn:
                lock_keys(conn, f"{lane}-high-water", ["key"])
                future = pool.submit(write, "key", seq=3, at=_AT)
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'direct_waiter' AND h.application_name = 'direct_holder')
                        """)
                        ).scalar_one()
                        if blocked or future.done():
                            break
                        Event().wait(0.02)
                    else:
                        pytest.fail("writer neither blocked nor completed")
                assert blocked is not omit
            future.result(timeout=10)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
