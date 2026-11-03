"""An arrival competitor cannot allocate from an uncommitted predecessor's snapshot."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import SystemClock
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import flow_store
from blizzard.hub.store.internal.chunk_rows import lock_keys
from tests.postgres_locks.test_flow_decisions import _AT, _TENANT, _seed
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_arrival_competitor_fences_allocation(database_url: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "arrival_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "arrival_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    held, waiting = scoped_connections(holder, _TENANT), scoped_connections(waiter, _TENANT)
    flows = flow_store.FlowStore(waiting, SystemClock())
    try:
        _seed(held)
        with held.write("seed_second") as conn:
            conn.execute(
                held.insert(s.chunks).values(chunk_id="ch_2", graph_id="gr_1", project_id="proj_1", minted_at=_AT)
            )

        def selective_lock(conn, namespace, keys):
            if not (omit and namespace == "flow-arrival"):
                lock_keys(conn, namespace, keys)

        with patch.object(flow_store, "lock_keys", selective_lock), ThreadPoolExecutor(max_workers=1) as pool:
            # The synthetic competitor takes the decision key without taking the tenant guard;
            # the real submit retains its ordinary exclusive tenant guard.
            with held.read("competing_arrival") as conn:
                lock_keys(conn, "flow-arrival", ["arrival"])
                future = pool.submit(flows.submit, "ch_2", node_id="nd_1", epoch=1, generation=0, resources=[], at=_AT)
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'arrival_waiter' AND h.application_name = 'arrival_holder')
                        """)
                        ).scalar_one()
                        if blocked or future.done():
                            break
                        Event().wait(0.02)
                    else:
                        pytest.fail("submit neither blocked nor completed")
                assert blocked is not omit
                if omit:
                    submission = future.result(timeout=5)
                    assert submission is not None and submission.arrival == 1
                insert = held.insert(s.flow_submissions).values(
                    submission_id="fs_competitor",
                    chunk_id="ch_1",
                    node_id="nd_1",
                    epoch=1,
                    generation=0,
                    arrival=1,
                    submitted_at=_AT,
                )
                if omit:
                    with pytest.raises(IntegrityError):
                        conn.execute(insert)
                    conn.rollback()
                else:
                    conn.execute(insert)
                    conn.commit()
            if not omit:
                submission = future.result(timeout=10)
                assert submission is not None and submission.arrival == 2
        with waiting.read("verify") as conn:
            assert conn.execute(waiting.select(s.flow_submissions, sa.func.count())).scalar_one() == (1 if omit else 2)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
