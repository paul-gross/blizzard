"""Transcript admission and low-level writes participate in decision and visibility locks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.observability.transcripts import TranscriptChunkMissing, TranscriptIngestService
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import transcript_segment_store as module
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections
from tests.test_transcript_ingest_service import _NO_RETIREMENTS
from tests.test_transcript_segment_store import _record

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 27, tzinfo=UTC)


def _seed(scoped: ScopedConnections) -> None:
    with scoped.founding("found", name="transcript", created_at=_AT, created_by="test"):
        pass
    with scoped.write("seed_chunk") as conn:
        conn.execute(
            scoped.insert(s.graphs).values(
                graph_id="graph", name="graph", entry_node_id="nd_build", definition_yaml="", created_at=_AT
            )
        )
        conn.execute(
            scoped.insert(s.projects).values(
                project_id="project",
                slug="project",
                name="project",
                description="",
                revision=1,
                color="blue",
                created_at=_AT,
            )
        )
        conn.execute(
            scoped.insert(s.chunks).values(chunk_id="ch_1", graph_id="graph", project_id="project", minted_at=_AT)
        )


def test_missing_chunk_rolls_back_batch_and_retries_when_chunk_exists(database_url: str) -> None:
    engine = sa.create_engine(database_url)
    try:
        scoped = scoped_connections(engine, TenantId("ten_transcript_locks"))
        _seed(scoped)
        store = module.TranscriptSegmentStore(scoped)
        service = TranscriptIngestService(store=store, retired=_NO_RETIREMENTS, clock=FixedClock(_AT))
        batch = [
            (1, _record()),
            (2, _record(segment_id="sg_missing", chunk_id="ch_missing")),
            (3, _record(segment_id="sg_later")),
        ]
        with pytest.raises(TranscriptChunkMissing):
            service.ingest("r1", batch)
        assert store.high_water("r1") == 0
        assert store.natural_key_state("sg_1", 0) == "absent"
        assert store.natural_key_state("sg_later", 0) == "absent"
        with scoped.write("create_chunk") as conn:
            conn.execute(
                scoped.insert(s.chunks).values(
                    chunk_id="ch_missing", graph_id="graph", project_id="project", minted_at=_AT
                )
            )
        result = service.ingest("r1", batch)
        assert result.already_applied == []
        assert result.applied == [1, 2, 3]
        assert result.high_water == 3
    finally:
        engine.dispose()


@pytest.mark.parametrize("direct", [False, True], ids=["admission", "direct-refresh"])
@pytest.mark.parametrize(
    ("namespace", "key"),
    [
        ("transcript-natural-key", '["sg_1", 0]'),
        ("transcript-chunk-budget", "ch_1"),
        ("transcript-runner-budget", "r1"),
        ("transcript-events-segment", "sg_1"),
        ("transcript-events-segment", "sg_new"),
        ("transcript-events-segment", "sg_old"),
    ],
)
@pytest.mark.parametrize("omit", [False, True], ids=["locked", "omission"])
def test_admission_waits_for_each_decision_key(
    database_url: str, direct: bool, namespace: str, key: str, omit: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "transcript_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "transcript_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    try:
        scoped = scoped_connections(holder, TenantId("ten_transcript_locks"))
        _seed(scoped)
        store = module.TranscriptSegmentStore(scoped_connections(waiter, TenantId("ten_transcript_locks")))
        store.insert_rejected(_record(supersedes="sg_old"), byte_count=1, reason="large", at=_AT)
        with scoped.write("seed_key") as conn:
            lock_keys(conn, namespace, [key])
        record = _record(supersedes="sg_new")
        if omit:

            def selective_lock(conn, lock_namespace, keys):
                lock_keys(conn, lock_namespace, [item for item in keys if (lock_namespace, item) != (namespace, key)])

            monkeypatch.setattr(module, "lock_keys", selective_lock)

        def act() -> None:
            if direct:
                store.update_to_accepted(record, byte_count=1, codec="zlib", at=_AT)
            else:
                with store.locked_ingest("r1", [(1, record)]):
                    pass

        with ThreadPoolExecutor(max_workers=1) as pool, scoped.read("hold_key") as conn:
            lock_keys(conn, namespace, [key])
            future = pool.submit(act)
            if omit:
                future.result(timeout=5)
            else:
                deadline = monotonic() + 5
                with observer.connect() as monitor:
                    while monotonic() < deadline:
                        blocked = monitor.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                              JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                              WHERE w.application_name = 'transcript_waiter'
                                AND h.application_name = 'transcript_holder')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not future.done(), "writer completed without the decision lock"
                        Event().wait(0.02)
                    else:
                        pytest.fail("writer did not block on the decision key")
            conn.commit()
            future.result(timeout=10)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
