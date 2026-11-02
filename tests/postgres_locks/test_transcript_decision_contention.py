"""Complete ingest decisions against independently committed PostgreSQL writes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.observability.transcripts import (
    TranscriptCaps,
    TranscriptChunkMissing,
    TranscriptIngestResult,
    TranscriptIngestService,
)
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerRetired
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import transcript_segment_store as module
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row, lock_keys
from blizzard.hub.store.internal.host_registry_store import lock_host_registry
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.postgres_locks.test_transcript_admission_locks import _AT, _seed
from tests.support import scoped_connections
from tests.test_transcript_ingest_service import _NO_RETIREMENTS
from tests.test_transcript_segment_store import _record

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("decision", ["natural", "chunk", "runner", "high-water", "retirement", "owner"])
@pytest.mark.parametrize("omit", [False, True], ids=["fenced", "omitted"])
def test_ingest_rechecks_independent_decision(database_url, monkeypatch, decision, omit):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "decision_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "decision_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        tenant = TenantId("ten_transcript_decisions")
        scoped = scoped_connections(holder, tenant)
        _seed(scoped)
        if decision == "retirement":
            RunnerRegistryStore(scoped).add(RunnerAddition("r1", "runner", "hash", _AT, "test"))
        with scoped.write("second_chunk") as conn:
            conn.execute(
                scoped.insert(s.chunks).values(chunk_id="ch_2", graph_id="graph", project_id="project", minted_at=_AT)
            )
        store = module.TranscriptSegmentStore(scoped_connections(waiter, tenant))
        if decision == "high-water":
            store.insert_accepted(_record(turns_json="old"), byte_count=3, codec="zlib", at=_AT)
        record = _record(turns_json="x" * 40, chunk_id="ch_missing" if decision == "natural" else "ch_1")
        caps = TranscriptCaps(
            chunk_budget_max_bytes=50 if decision == "chunk" else 1000,
            runner_daily_rate_max_bytes=50 if decision == "runner" else 1000,
        )
        service = TranscriptIngestService(store=store, retired=_NO_RETIREMENTS, clock=FixedClock(_AT), caps=caps)
        read_mark = Event()
        continue_ingest = Event()
        namespaces = {
            "natural": "transcript-natural-key",
            "chunk": "transcript-chunk-budget",
            "runner": "transcript-runner-budget",
            "high-water": "transcript-high-water",
        }
        if omit and decision in namespaces:

            def selective(conn, namespace, keys):
                if namespace != namespaces[decision]:
                    lock_keys(conn, namespace, keys)

            monkeypatch.setattr(module, "lock_keys", selective)
        elif omit and decision == "retirement":
            monkeypatch.setattr(module, "lock_host_registry", lambda conn: None)
        elif omit and decision == "owner":
            monkeypatch.setattr(module, "lock_chunk_row", lambda conn, chunk_id: None)
        original_high_water = module.TranscriptSegmentStore.high_water
        if omit and decision == "high-water":

            def stale_high_water(self, runner_id):
                mark = original_high_water(self, runner_id)
                assert mark == 0
                read_mark.set()
                assert continue_ingest.wait(5)
                return mark

            monkeypatch.setattr(module.TranscriptSegmentStore, "high_water", stale_high_water)

        def ingest():
            try:
                return service.ingest("r1", [(1, record)])
            except (TranscriptChunkMissing, RunnerRetired) as exc:
                return exc

        with ThreadPoolExecutor(max_workers=1) as pool, scoped.read("independent_winner") as conn:
            if decision in ("natural", "chunk", "runner"):
                key = {"natural": '["sg_1", 0]', "chunk": "ch_1", "runner": "r1"}[decision]
                lock_keys(conn, namespaces[decision], [key])
                competing = _record(
                    segment_id="sg_1" if decision == "natural" else "sg_other",
                    chunk_id="ch_2" if decision in ("natural", "runner") else "ch_1",
                    runner_id="r2" if decision == "chunk" else "r1",
                    turns_json="y" * 40,
                )
                conn.execute(
                    module._insert_accepted_stmt(
                        scoped,
                        competing,
                        byte_count=40,
                        codec="zlib",
                        content=module.TranscriptSegmentStore._compress(competing.turns_json, "zlib"),
                        at=_AT,
                    )
                )
            elif decision == "high-water":
                lock_keys(conn, namespaces[decision], ["r1"])
                conn.execute(scoped.insert(s.transcript_high_water).values(runner_id="r1", seq=1, updated_at=_AT))
            elif decision == "retirement":
                lock_host_registry(conn)
                conn.execute(
                    scoped.insert(s.runner_lifecycle_facts).values(
                        runner_id="r1", retired=True, set_at=_AT, set_by="test"
                    )
                )
            else:
                lock_chunk_row(conn, "ch_1")
                conn.execute(
                    scoped.insert(s.epoch_owners).values(chunk_id="ch_1", epoch=1, runner_id="r2", recorded_at=_AT)
                )
            future = pool.submit(ingest)
            if omit and decision == "high-water":
                assert read_mark.wait(5), "ingest did not read the stale high-water"
                conn.commit()
                continue_ingest.set()
                future.result(timeout=5)
                monkeypatch.setattr(module.TranscriptSegmentStore, "high_water", original_high_water)
            elif omit:
                future.result(timeout=5)
            else:
                deadline = monotonic() + 5
                with observer.connect().execution_options(isolation_level="AUTOCOMMIT") as monitor:
                    while monotonic() < deadline:
                        blocked = monitor.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'decision_waiter' AND h.application_name = 'decision_holder')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not future.done(), "ingest bypassed the decision"
                        Event().wait(0.02)
                    else:
                        pytest.fail("PostgreSQL did not report the decision wait")
            conn.commit()
            result = future.result(timeout=10)

        if decision == "natural" and omit:
            assert isinstance(result, TranscriptChunkMissing)
        elif decision == "retirement" and not omit:
            assert isinstance(result, RunnerRetired)
        else:
            assert isinstance(result, TranscriptIngestResult)
            if decision in ("natural", "retirement"):
                assert result.applied == [1]
            elif decision == "owner":
                assert (result.applied, result.refused) == (([1], []) if omit else ([], [1]))
            elif decision in ("chunk", "runner"):
                assert (result.applied, result.capped) == (([1], []) if omit else ([], [1]))
            else:
                assert (result.applied, result.already_applied) == (([1], []) if omit else ([], [1]))
        assert store.high_water("r1") == (
            0 if (decision == "natural" and omit) or (decision == "retirement" and not omit) else 1
        )
        with scoped.read("verify_decision") as conn:
            rows = conn.execute(scoped.select(s.transcript_segments)).all()
        offered = [row for row in rows if row.segment_id == "sg_1"]
        if decision == "natural":
            assert len(offered) == 1 and offered[0].chunk_id == "ch_2"
        elif decision in ("retirement", "owner") and not omit:
            assert offered == []
        else:
            assert len(offered) == 1
            if decision in ("chunk", "runner") and not omit:
                assert offered[0].rejected
                assert offered[0].rejection_reason == (
                    "chunk_budget_exceeded" if decision == "chunk" else "runner_daily_rate_exceeded"
                )
            else:
                assert not offered[0].rejected
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
