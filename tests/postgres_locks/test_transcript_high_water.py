"""The first transcript high-water write is serialized by its runner key."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.hub.store.internal import transcript_segment_store
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 27, tzinfo=UTC)


@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_first_write_waits_before_reading_high_water(database_url: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    engine = sa.create_engine(database_url, connect_args={"application_name": "transcript_waiter", "options": options})
    holder = sa.create_engine(database_url, connect_args={"application_name": "transcript_holder", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    scoped = scoped_connections(engine, TenantId("ten_transcript_lock"))
    holder_scoped = scoped_connections(holder, TenantId("ten_transcript_lock"))
    store = transcript_segment_store.TranscriptSegmentStore(scoped)
    try:
        with scoped.founding("found", name="transcript-test", created_at=_AT, created_by="test"):
            pass
        # Seed only the lock row: the high-water row must still be absent.
        with scoped.write("seed") as conn:
            lock_keys(conn, "transcript-high-water", ["r1"])
        with holder.begin() as conn:
            result = conn.execute(
                sa.text(
                    "UPDATE keyed_locks SET key = key WHERE tenant_id = :tenant AND namespace = :namespace AND key = :key"
                ),
                {"tenant": "ten_transcript_lock", "namespace": "transcript-high-water", "key": "r1"},
            )
            assert result.rowcount == 1
            with (
                patch.object(transcript_segment_store, "lock_keys", (lambda *_: None) if omit else lock_keys),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(store.set_high_water, "r1", seq=3, at=_AT)
                try:
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text("""
                                    SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                    JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                    WHERE w.application_name = 'transcript_waiter'
                                    AND h.application_name = 'transcript_holder')
                                """)
                            ).scalar_one()
                            if blocked or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("writer neither completed nor blocked")
                    assert blocked is not omit
                    if not omit:
                        conn.execute(
                            transcript_segment_store._insert_high_water_stmt(holder_scoped, "r1", seq=9, at=_AT)
                        )
                finally:
                    conn.commit()
                future.result(timeout=10)
        assert store.high_water("r1") == (3 if omit else 9)
    finally:
        engine.dispose()
        holder.dispose()
        observer.dispose()
