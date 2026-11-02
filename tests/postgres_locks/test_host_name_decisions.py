"""Host name decisions observe the committed winner under the narrow registry lock."""

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

import blizzard.hub.store.internal.host_registry_store as host_module
from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.runners.host import HostAddition, HostReport
from blizzard.hub.domain.runners.host_report import HostReportService
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 20, tzinfo=UTC)


def _blocked(observer: sa.Engine, future: Future) -> None:
    deadline = monotonic() + 5
    with observer.connect() as conn:
        while monotonic() < deadline:
            if conn.execute(
                sa.text("""
                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                  WHERE w.application_name = 'name_waiter' AND h.application_name = 'name_holder')
            """)
            ).scalar_one():
                return
            assert not future.done(), "name decision completed without the holder's lock"
            Event().wait(0.02)
    pytest.fail("name decision did not block on the holder")


@pytest.mark.parametrize("scenario", ["report", "retirement", "self", "omission"])
def test_host_name_decision_after_registry_holder(
    database_url: str, scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "name_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "name_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    tenant = TenantId("ten_host_name_decision")
    try:
        scope = scoped_connections(holder, tenant)
        with scope.founding("found", name="host-name", created_at=_AT, created_by="test"):
            pass
        store = HostRegistryStore(scoped_connections(waiter, tenant))
        store.add(HostAddition("first", "one", "hash-one", _AT, "test"))
        store.add(HostAddition("second", "two", "hash-two", _AT, "test"))
        held = store.get_host("second")
        assert held is not None
        service = HostReportService(hosts=store, clock=FixedClock(_AT))
        report = HostReport("two" if scenario == "self" else "shared", "http://second", (), (), ())

        if scenario == "omission":
            monkeypatch.setattr(host_module, "lock_host_registry", lambda conn: None)
            monkeypatch.setattr(host_module, "lock_keys", lambda *args: None)

        def act():
            if scenario == "retirement":
                return store.add(HostAddition("third", "one", "hash-three", _AT, "test"))
            return service.report(held, report)

        with ThreadPoolExecutor(max_workers=1) as actors:
            # A scoped read transaction takes no tenant admission lock. Seed the opposing
            # decision directly while holding the same narrow key used by the writers.
            with scope.read("hold_host_registry") as conn:
                lock_keys(conn, "host-live-name-registry", ["registry"])
                if scenario == "report":
                    conn.execute(scope.update(s.hosts).where(s.hosts.c.host_id == "first").values(name="shared"))
                elif scenario == "retirement":
                    conn.execute(
                        scope.insert(s.host_lifecycle_facts).values(
                            host_id="first", retired=True, set_at=_AT, set_by="test"
                        )
                    )
                future = actors.submit(act)
                if scenario == "omission":
                    result = future.result(timeout=5)
                    assert result is not None
                    assert result.name == "shared"
                else:
                    _blocked(observer, future)
                    conn.commit()
            result = future.result(timeout=10)

        first = store.get_host("first")
        second = store.get_host("second")
        assert first is not None and second is not None
        if scenario == "report":
            assert result is not None
            assert first.name == "shared"
            assert result.name == second.name == "two"
            assert result.first and second.last_seen_at == _AT and second.url == "http://second"
            assert store.live_names() == {"shared", "two"}
        elif scenario == "retirement":
            third = store.get_host("third")
            assert first.retired and third is not None and third.name == "one" and not third.retired
            assert store.live_names() == {"one", "two"}
        else:
            assert result is not None
            assert result.name == second.name == ("two" if scenario == "self" else "shared")
            assert result.first and second.last_seen_at == _AT
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
