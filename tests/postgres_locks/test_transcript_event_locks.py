"""Transcript derivation writes serialize by tenant and segment, independently of admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.hub.store.internal import transcript_event_store as module
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections
from tests.test_transcript_event_store import _PROVENANCE

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 27, tzinfo=UTC)


def _replace(store: module.TranscriptEventStore, segment: str) -> None:
    store.replace_segment_events(
        segment, "v1", [], complete=True, content_fingerprint="new", at=_AT, provenance=_PROVENANCE
    )


@pytest.mark.parametrize("operation", ["drop", "replace"])
@pytest.mark.parametrize("omit", [False, True], ids=["locked", "omission"])
def test_segment_writer_waits_on_shared_key(
    database_url: str, operation: str, omit: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "event_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "event_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    tenant = TenantId("ten_event_lock")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="event-lock", created_at=_AT, created_by="test"):
            pass
        with scoped.write("seed_lock") as conn:
            lock_keys(conn, module._SEGMENT_LOCK_NAMESPACE, ["sg_1"])
        store = module.TranscriptEventStore(scoped_connections(waiter, tenant))
        if omit:
            monkeypatch.setattr(module, "lock_keys", lambda *args: None)

        def act() -> None:
            if operation == "drop":
                store.drop_segments(frozenset({"sg_1"}), at=_AT)
            else:
                _replace(store, "sg_1")

        with ThreadPoolExecutor(max_workers=1) as pool:
            # read() avoids holding the tenant admission lock; only the segment key blocks.
            with scoped.read("hold_segment") as conn:
                lock_keys(conn, module._SEGMENT_LOCK_NAMESPACE, ["sg_1"])
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
                                  WHERE w.application_name = 'event_waiter'
                                    AND h.application_name = 'event_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "writer completed without the segment lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("writer did not block on the segment key")
                # A concurrent derivation committed before the drop's deletes must be removed.
                if operation == "drop":
                    conn.execute(
                        module._upsert_marker_stmt(
                            scoped, "sg_1", "v2", content_fingerprint="old", event_count=0, complete=True, at=_AT
                        )
                    )
                    conn.commit()
            future.result(timeout=10)
        if operation == "drop":
            assert (store.derivation_marker("sg_1", "v2") is not None) is omit
        else:
            marker = store.derivation_marker("sg_1", "v1")
            assert marker is None  # the missing segment is rejected after acquiring the key
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


def test_segment_keys_are_tenant_and_segment_scoped(database_url: str) -> None:
    engine = sa.create_engine(database_url, connect_args={"options": "-c statement_timeout=8000"})
    try:
        first = scoped_connections(engine, TenantId("ten_event_a"))
        second = scoped_connections(engine, TenantId("ten_event_b"))
        for scoped in (first, second):
            with scoped.founding("found", name="event-lock", created_at=_AT, created_by="test"):
                pass
        with first.write("seed_lock") as conn:
            lock_keys(conn, module._SEGMENT_LOCK_NAMESPACE, ["sg_1"])
        with ThreadPoolExecutor(max_workers=2) as pool, first.read("hold_segment") as conn:
            lock_keys(conn, module._SEGMENT_LOCK_NAMESPACE, ["sg_1"])
            other_segment = pool.submit(_replace, module.TranscriptEventStore(first), "sg_2")
            other_tenant = pool.submit(_replace, module.TranscriptEventStore(second), "sg_1")
            other_segment.result(timeout=5)
            other_tenant.result(timeout=5)
        module.TranscriptEventStore(first).drop_segments(frozenset({"sg_1", "sg_2"}), at=_AT)
        assert module.TranscriptEventStore(second).derivation_marker("sg_1", "v1") is None
    finally:
        engine.dispose()
