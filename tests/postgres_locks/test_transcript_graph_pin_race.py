"""A transition cannot overtake the pin guard of a transcript replacement."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import decide_close_enqueue
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import transcript_event_store as event_module
from blizzard.hub.store.internal.chunk_movement_store import ChunkMovementStore
from blizzard.hub.store.internal.hub_scoped_corpus import SweepCorpusReads
from blizzard.hub.store.internal.hub_scoped_reads import hub_scoped_read
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import TenantId
from tests.support import hub_level_connections, scoped_connections
from tests.test_transcript_event_derivation import _segment_record
from tests.test_transcript_event_store import _PROVENANCE, _event

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 27, tzinfo=UTC)


@pytest.mark.parametrize("omit", [False, True], ids=["locked", "omit-replacement-lock"])
def test_transition_winner_fences_stale_replacement(
    database_url: str, omit: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    writer = sa.create_engine(database_url, connect_args={"application_name": "pin_writer", "options": options})
    reader = sa.create_engine(database_url, connect_args={"application_name": "pin_replace", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    scope = scoped_connections(writer, TenantId("ten_pin_race"))
    reached, release = Event(), Event()
    try:
        with scope.founding("found", name="pin-race", created_at=_AT, created_by="test"):
            pass
        with scope.write("seed") as conn:
            for graph in ("old", "new"):
                conn.execute(
                    scope.insert(s.graphs).values(
                        graph_id=graph, name=graph, entry_node_id="nd_build", definition_yaml="", created_at=_AT
                    )
                )
            conn.execute(
                scope.insert(s.projects).values(
                    project_id="p", slug="p", name="p", description="", revision=1, color="blue", created_at=_AT
                )
            )
            conn.execute(scope.insert(s.chunks).values(chunk_id="ch_1", graph_id="new", project_id="p", minted_at=_AT))
            conn.execute(
                scope.insert(s.transitions).values(
                    transition_id="old_transition",
                    chunk_id="ch_1",
                    graph_id="old",
                    to_node_id="nd_build",
                    epoch=1,
                    runner_id="r1",
                    recorded_at=_AT,
                )
            )
        TranscriptSegmentStore(scope).insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_AT)
        store = event_module.TranscriptEventStore(scoped_connections(reader, scope.scope.tenant))
        current = store.segment_derivation_input("sg_1")
        assert current is not None
        movement = ChunkMovementStore(scope, FixedClock(_AT))

        # Shared admission prevents the tenant row from hiding a missing chunk lock.
        def shared_admission(store: ScopedConnections, conn: sa.Connection) -> None:
            conn.execute(
                sa.select(s.tenants.c.tenant_id)
                .where(s.tenants.c.tenant_id == store.scope.tenant)
                .with_for_update(read=True)
            ).one()

        monkeypatch.setattr(ScopedConnections, "_lock_open_tenant", shared_admission)
        if omit:
            monkeypatch.setattr(event_module, "lock_chunk_row", lambda conn, chunk_id: None)

        @event.listens_for(writer, "after_cursor_execute")
        def pause(conn, cursor, statement, parameters, context, executemany):
            if statement.lower().lstrip().startswith("insert into transitions") and not reached.is_set():
                reached.set()
                assert release.wait(8)

        def transition():
            return movement.record_transition(
                transition_id="new_transition",
                chunk_id="ch_1",
                from_node_id="previous",
                to_node_id="nd_build",
                choice_name=None,
                epoch=1,
                admission=EpochAdmission.AT_OR_ABOVE,
                runner_id="r1",
                at=_AT + timedelta(seconds=1),
                artifacts=[],
                close_decision=decide_close_enqueue([], lambda _: True),
            )

        def replace():
            return store.replace_segment_events(
                "sg_1",
                "v1",
                [_event(graph_id="old")],
                complete=True,
                content_fingerprint=current.content_fingerprint,
                at=_AT,
                provenance=_PROVENANCE,
                expected_pin=("ch_1", "old"),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            transition_future = pool.submit(transition)
            try:
                assert reached.wait(8)
                replacement = pool.submit(replace)
                if omit:
                    assert replacement.result(timeout=5)
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity w JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid)) WHERE w.application_name='pin_replace' AND h.application_name='pin_writer')"
                                )
                            ).scalar_one()
                            if blocked:
                                break
                            assert not replacement.done(), "replacement passed the chunk lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("replacement did not wait for the transition")
            finally:
                release.set()
            assert transition_future.result(timeout=10) is None
            assert replacement.result(timeout=10) is omit
        with scope.read("inspect") as conn:
            rows = conn.execute(scope.select(s.transcript_events)).all()
        assert [row.graph_id for row in rows] == (["old"] if omit else [])
        assert (store.derivation_marker("sg_1", "v1") is not None) is omit
        assert store.candidacy("v1").candidate_segment_ids == ["sg_1"]
        corpus = SweepCorpusReads(partial(hub_scoped_read, hub_level_connections(reader)))
        assert corpus.derivation_signature()[scope.scope.tenant].transition_count == 2
        assert store.derivation_signature() == corpus.derivation_signature()[scope.scope.tenant]
        assert corpus.candidacy("v1")[scope.scope.tenant].candidate_segment_ids == ["sg_1"]
    finally:
        release.set()
        writer.dispose()
        reader.dispose()
        observer.dispose()
