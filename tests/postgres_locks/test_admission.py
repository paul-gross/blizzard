"""A live baseline: a scoped writer waits for another writer's tenant admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


def test_existing_tenant_admission_serializes_writers(database_url: str) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(
        database_url, connect_args={"application_name": "blizzard_lock_holder", "options": options}
    )
    waiter = sa.create_engine(
        database_url, connect_args={"application_name": "blizzard_lock_waiter", "options": options}
    )
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    tenant = TenantId("ten_lock_test")
    entered = Event()
    release = Event()
    try:
        with scoped_connections(holder, tenant).founding(
            "found", name="lock-test", created_at=datetime.now(UTC), created_by="test"
        ):
            pass

        def first() -> None:
            with scoped_connections(holder, tenant).write("first"):
                entered.set()
                assert release.wait(10), "holder timed out"

        def second() -> None:
            assert entered.wait(10), "holder did not enter"
            with scoped_connections(waiter, tenant).write("second"):
                pass

        with ThreadPoolExecutor(max_workers=2) as actors:
            a = actors.submit(first)
            b = actors.submit(second)
            try:
                assert entered.wait(10), "holder did not enter"
                deadline = monotonic() + 5
                with observer.connect() as conn:
                    while monotonic() < deadline:
                        blocked = conn.execute(
                            sa.text("""
                            SELECT EXISTS (
                              SELECT 1 FROM pg_stat_activity AS waiter
                              JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                              WHERE waiter.application_name = 'blizzard_lock_waiter'
                                AND holder.application_name = 'blizzard_lock_holder'
                            )
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not b.done(), "writer entered without waiting"
                        Event().wait(0.02)
                    else:
                        pytest.fail("PostgreSQL did not report the expected blocking relationship")
            finally:
                release.set()
            a.result(timeout=10)
            b.result(timeout=10)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
