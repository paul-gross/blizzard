"""Close and teardown completion serialize on the tenant row in either order."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from blizzard.hub.domain.tenancy.teardown import CloseOutcome
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.tenant_teardown_store import TenantTeardownStore
from blizzard.hub.tenancy import TenantId
from tests.support import hub_level_connections, scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("first", ["close", "complete"])
@pytest.mark.parametrize("same_tenant", [True, False])
def test_close_and_completion_serialize(database_url: str, first: str, same_tenant: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "teardown_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "teardown_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    tenant, other, spare = map(TenantId, ("ten_a", "ten_b", "ten_c"))
    target = tenant if same_tenant else other
    now = datetime.now(UTC)
    entered, release = Event(), Event()
    try:
        for each in (tenant, other, spare):
            with scoped_connections(holder, each).founding("found", name=each, created_at=now, created_by="test"):
                pass
        holder_store = TenantTeardownStore(hub_level_connections(holder))
        waiter_store = TenantTeardownStore(hub_level_connections(waiter))
        assert holder_store.close(tenant, by="initial", at=now) is CloseOutcome.CLOSED

        @event.listens_for(holder, "after_cursor_execute")
        def pause_with_lock(conn, cursor, statement, parameters, context, executemany):
            sql = statement.lower()
            # Pause only after close locks the tenant being completed, not an earlier row.
            if (first == "close" and sql.startswith("update tenants") and parameters["tenant_id_1"] == tenant) or (
                first == "complete" and sql.startswith("delete from tenants")
            ):
                entered.set()
                assert release.wait(10), "holder timed out"

        def close(store: TenantTeardownStore) -> CloseOutcome:
            return store.close(target, by="concurrent", at=now)

        def complete(store: TenantTeardownStore) -> int | None:
            return store.complete(tenant, at=now)

        with ThreadPoolExecutor(max_workers=2) as actors:
            a = actors.submit(close if first == "close" else complete, holder_store)
            try:
                assert entered.wait(5), "holder did not acquire the tenant row"
                b = actors.submit(complete if first == "close" else close, waiter_store)
                deadline = monotonic() + 5
                with observer.connect() as monitor:
                    while monotonic() < deadline:
                        blocked = monitor.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'teardown_waiter'
                              AND h.application_name = 'teardown_holder')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not b.done(), "operation completed without waiting"
                        Event().wait(0.02)
                    else:
                        pytest.fail("PostgreSQL did not report the expected blocking relationship")
            finally:
                release.set()
            first_result = a.result(timeout=10)
            second_result = b.result(timeout=10)

        close_result = first_result if first == "close" else second_result
        completion_result = second_result if first == "close" else first_result
        expected = (
            CloseOutcome.CLOSED
            if not same_tenant
            else (CloseOutcome.UNDERWAY if first == "close" else CloseOutcome.DELETED)
        )
        assert close_result is expected
        assert completion_result == 1
        fact = holder_store.deletion(tenant)
        assert fact is not None and fact.deleted_at == now and fact.rows_removed == 1
        with observer.connect() as conn:
            assert conn.execute(sa.select(s.tenants.c.tenant_id).where(s.tenants.c.tenant_id == tenant)).first() is None
        if not same_tenant:
            other_fact = holder_store.deletion(other)
            assert other_fact is not None and other_fact.deleted_at is None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
