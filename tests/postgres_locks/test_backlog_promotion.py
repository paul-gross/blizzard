"""Queue position writers and promotion share the queue key."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.store.internal import chunk_exclusive_store, chunk_queue_store
from blizzard.hub.store.internal.queue_lock import lock_queue_positions
from tests.support import chunk_stores, hub_store_connections, seed_chunk_record, seed_graph

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("operation", ["queue", "backlog", "promote", "exclusive"])
@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_queue_key_blocks_writer(database_url: str, operation: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    waiter = sa.create_engine(database_url, connect_args={"application_name": "queue_waiter", "options": options})
    holder = sa.create_engine(database_url, connect_args={"application_name": "queue_holder", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        with waiter.begin() as conn:
            seed_graph(conn, "gr_queue", at=_AT)
        stores = chunk_stores(waiter, FixedClock(_AT))
        seed_chunk_record(stores, Chunk(chunk_id="ch_queue", graph_id="gr_queue", work_refs=[], minted_at=_AT))
        seed_chunk_record(stores, Chunk(chunk_id="ch_earlier", graph_id="gr_queue", work_refs=[], minted_at=_AT))

        def actor() -> None:
            if operation == "queue":
                stores.queue.record_queue_positions([("ch_queue", 3.0), ("ch_earlier", 2.0)], at=_AT)
            elif operation == "backlog":
                stores.queue.record_backlog_positions([("ch_queue", 3.0), ("ch_earlier", 2.0)], at=_AT)
            elif operation == "promote":
                stores.queue.record_promote("ch_queue", at=_AT)
            else:
                with stores.exclusive.locked_queue(["ch_queue"]):
                    pass

        module = chunk_exclusive_store if operation == "exclusive" else chunk_queue_store
        original = module.lock_queue_positions
        with hub_store_connections(holder).read("hold queue key") as conn:
            lock_queue_positions(conn)
            with patch.object(module, "lock_queue_positions", (lambda *_: None) if omit else original):  # noqa: SIM117
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(actor)
                    try:
                        deadline = monotonic() + 5
                        with observer.connect() as watch:
                            while monotonic() < deadline:
                                blocked = watch.execute(
                                    sa.text("""
                                    SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                    JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                    WHERE w.application_name = 'queue_waiter'
                                    AND h.application_name = 'queue_holder')
                                """)
                                ).scalar_one()
                                if blocked or future.done():
                                    break
                                Event().wait(0.02)
                            else:
                                pytest.fail("writer neither blocked nor completed")
                        assert blocked is not omit
                    finally:
                        conn.commit()
                    future.result(timeout=10)
    finally:
        waiter.dispose()
        holder.dispose()
        observer.dispose()
