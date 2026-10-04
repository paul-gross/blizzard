"""The operational event log: store writes/reads, feed derivation, migration (#125).

``event_log`` is an append-only operational-fact table (``bzh:facts-not-status``): one
row per thing that happened, no status column. These tests pin round-trip, filtering,
supersession, feed ordering, and the migration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert, select

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.fence import EpochAdmission
from blizzard.hub.domain.chunks.stores import ChunkStores
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.work import EscalationOpen, EventFeed, EventRow
from blizzard.hub.store import schema as s
from tests.support import chunk_stores, count_queries, migrate_to, seed_chunk, seed_graph, seed_lease

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_HEAD_BEFORE_EVENT_LOG = "20260721_1500_hub_cli_auth_state_user"


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _store(tmp_path: Path) -> tuple[ChunkStores, Engine, FixedClock]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_a", graph_id="gr_1", at=_T0)
        seed_chunk(conn, "ch_b", graph_id="gr_1", at=_T0)
    clock = FixedClock(_T0)
    return chunk_stores(engine, clock), engine, clock


def test_record_event_roundtrips_columns_and_json_detail(tmp_path: Path) -> None:
    store, _, _clock = _store(tmp_path)
    store.events.record_event(
        severity="critical",
        kind="worker-lost",
        runner_id="runner-1",
        chunk_id="ch_a",
        lease_id="lease-1",
        node_name="build",
        message="worker exited without a session-end",
        detail={"via": "advance", "reason": "failed", "stderr_tail": "boom"},
        at=_at(10),
    )
    (row,) = store.events.list_events()
    assert row.severity == "critical"
    assert row.kind == "worker-lost"
    assert row.runner_id == "runner-1"
    assert row.chunk_id == "ch_a"
    assert row.lease_id == "lease-1"
    assert row.node_name == "build"
    assert row.message == "worker exited without a session-end"
    assert row.detail == {"via": "advance", "reason": "failed", "stderr_tail": "boom"}
    assert row.recorded_at == _at(10)
    assert row.id > 0


def test_runner_scoped_event_carries_no_chunk(tmp_path: Path) -> None:
    store, _, _clock = _store(tmp_path)
    store.events.record_event(
        severity="warning",
        kind="command-failed",
        runner_id="runner-1",
        chunk_id=None,
        lease_id=None,
        node_name=None,
        message="git push failed",
        detail=None,
        at=_at(5),
    )
    (row,) = store.events.list_events()
    assert row.chunk_id is None
    assert row.lease_id is None
    assert row.detail is None


def test_list_events_filters_and_orders_newest_first_bounded(tmp_path: Path) -> None:
    store, _, _clock = _store(tmp_path)
    store.events.record_event(
        severity="info",
        kind="attempt-abandoned",
        runner_id="r1",
        chunk_id="ch_a",
        lease_id=None,
        node_name=None,
        message="a",
        detail=None,
        at=_at(1),
    )
    store.events.record_event(
        severity="warning",
        kind="attempt-failed",
        runner_id="r1",
        chunk_id="ch_a",
        lease_id=None,
        node_name=None,
        message="b",
        detail=None,
        at=_at(2),
    )
    store.events.record_event(
        severity="critical",
        kind="worker-lost",
        runner_id="r2",
        chunk_id="ch_b",
        lease_id=None,
        node_name=None,
        message="c",
        detail=None,
        at=_at(3),
    )

    # Newest-first over recorded_at.
    assert [e.message for e in store.events.list_events()] == ["c", "b", "a"]
    # Filters.
    assert [e.message for e in store.events.list_events(severity="warning")] == ["b"]
    assert [e.message for e in store.events.list_events(runner_id="r2")] == ["c"]
    assert [e.message for e in store.events.list_events(chunk_id="ch_a")] == ["b", "a"]
    assert [e.message for e in store.events.list_events(since=_at(2))] == ["c", "b"]
    # Bounded.
    assert [e.message for e in store.events.list_events(limit=1)] == ["c"]


def test_list_events_cap_keeps_the_newest_rows_regardless_of_severity(tmp_path: Path) -> None:
    store, _, _clock = _store(tmp_path)
    store.events.record_event(
        severity="critical",
        kind="worker-lost",
        runner_id="r1",
        chunk_id="ch_a",
        lease_id=None,
        node_name=None,
        message="old-critical",
        detail=None,
        at=_at(1),
    )
    for sec in (2, 3, 4, 5):
        store.events.record_event(
            severity="warning" if sec % 2 else "info",
            kind="attempt-failed",
            runner_id="r1",
            chunk_id="ch_a",
            lease_id=None,
            node_name=None,
            message=f"row-{sec}",
            detail=None,
            at=_at(sec),
        )

    # A limit=3 read over 5 rows drops the old critical for the newer warning/info rows.
    assert [e.message for e in store.events.list_events(limit=3)] == ["row-5", "row-4", "row-3"]
    assert [e.message for e in store.events.list_events()][-1] == "old-critical"


def test_list_open_escalations_applies_supersession_fleet_wide(tmp_path: Path) -> None:
    store, engine, clock = _store(tmp_path)
    with engine.begin() as conn:  # seed the requeue and stop cases' chunks
        seed_chunk(conn, "ch_c", graph_id="gr_1", at=_T0)
        seed_chunk(conn, "ch_d", graph_id="gr_1", at=_T0)
        seed_chunk(conn, "ch_e", graph_id="gr_1", at=_T0)
        seed_chunk(conn, "ch_f", graph_id="gr_1", at=_T0)

    # ch_a: escalation, nothing after it -> OPEN.
    store.escalations.record_escalation(
        "ch_a",
        epoch=1,
        takeover_command="cd a && resume",
        at=_at(10),
        admission=EpochAdmission.AT_OR_ABOVE,
        cause=None,
        detail=None,
    )
    # ch_b: escalation then a LATER lease mint -> superseded (closed).
    store.escalations.record_escalation(
        "ch_b",
        epoch=1,
        takeover_command="cd b && resume",
        at=_at(10),
        admission=EpochAdmission.AT_OR_ABOVE,
        cause=None,
        detail=None,
    )
    seed_lease(engine, "ch_b", epoch=2, runner_id="r1", at=_at(20))
    # ch_c: escalation then a LATER requeue -> superseded (closed).
    store.escalations.record_escalation(
        "ch_c",
        epoch=1,
        takeover_command="cd c && resume",
        at=_at(10),
        admission=EpochAdmission.AT_OR_ABOVE,
        cause=None,
        detail=None,
    )
    with store.exclusive.locked(["ch_c"]) as handle:
        store.movement.record_requeue_locked(handle, "ch_c", at=_at(20))
    # ch_d: escalation then a LATER stop -> superseded (#292). This read feeds the critical
    # `needs-human` row in `GET /api/events`, so a stopped chunk must leave it.
    store.escalations.record_escalation(
        "ch_d",
        epoch=1,
        takeover_command="cd d && resume",
        at=_at(10),
        admission=EpochAdmission.AT_OR_ABOVE,
        cause=None,
        detail=None,
    )
    clock.instant = _at(20)
    with store.exclusive.locked(["ch_d"]) as handle:
        store.lifecycle.record_stop_locked(handle, "ch_d", by="operator", at=_at(20))
    # ch_e: stop then a LATER escalation -> still dropped; a stopped chunk is terminal, so the
    # store query excludes it before the escalation's ordering is ever consulted.
    clock.instant = _at(10)
    with store.exclusive.locked(["ch_e"]) as handle:
        store.lifecycle.record_stop_locked(handle, "ch_e", by="operator", at=_at(10))
    # Seeded as a raw row: the write fence refuses an escalation on a stopped chunk, so the
    # store cannot produce this ordering — only the read's exclusion can be pinned.
    with engine.begin() as conn:
        conn.execute(
            insert(s.escalations).values(
                chunk_id="ch_e",
                epoch=1,
                takeover_command="cd e && resume",
                wrapped_takeover_command="",
                recorded_at=_at(20),
            )
        )
    # ch_f: escalation then the chunk REACHES DONE elsewhere -> superseded (#293). No later
    # lease is minted here, so completion is the only arm that can close it.
    store.escalations.record_escalation(
        "ch_f",
        epoch=1,
        takeover_command="cd f && resume",
        at=_at(10),
        admission=EpochAdmission.AT_OR_ABOVE,
        cause=None,
        detail=None,
    )
    store.movement.record_transition(
        transition_id="tr_f1",
        chunk_id="ch_f",
        from_node_id=None,
        to_node_id=RESERVED_TERMINAL,
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=_at(20),
        artifacts=[],
        proposals=[],
        admission=EpochAdmission.AT_OR_ABOVE,
    )

    # ch_f drops because it DERIVES done, not incidentally — pin the mechanism, not the count.
    ch_f_facts = store.facts.load_facts("ch_f")
    assert ch_f_facts is not None and ch_f_facts.status() is ChunkStatus.DONE

    opens = store.escalations.list_open_escalations()
    assert sorted(e.chunk_id for e in opens) == ["ch_a"]
    assert next(e.takeover_command for e in opens if e.chunk_id == "ch_a") == "cd a && resume"


@pytest.mark.component
def test_list_open_escalations_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    """``list_open_escalations``'s ``load_facts_for`` batch costs a bounded number of
    statements per read, not one more per candidate chunk as the fleet grows."""

    def _fleet(root: Path, n: int) -> tuple[ChunkStores, Engine]:
        root.mkdir()
        _, engine = migrate_to(root, "head")
        with engine.begin() as conn:
            seed_graph(conn, "gr_1", at=_T0)
            for i in range(n):
                seed_chunk(conn, f"ch_{i}", graph_id="gr_1", at=_T0)
        store = chunk_stores(engine, FixedClock(_T0))
        for i in range(n):
            store.escalations.record_escalation(
                f"ch_{i}",
                epoch=1,
                takeover_command="cd a && resume",
                at=_at(10),
                admission=EpochAdmission.AT_OR_ABOVE,
                cause=None,
                detail=None,
            )
        return store, engine

    small, small_engine = _fleet(tmp_path / "small", 3)
    large, large_engine = _fleet(tmp_path / "large", 9)

    small_count = count_queries(small_engine, lambda: small.escalations.list_open_escalations())
    large_count = count_queries(large_engine, lambda: large.escalations.list_open_escalations())

    assert len(small.escalations.list_open_escalations()) == 3
    assert len(large.escalations.list_open_escalations()) == 9
    assert small_count == large_count


def test_event_feed_sorts_by_recency_across_severities() -> None:
    events = [
        EventRow(
            id=1,
            recorded_at=_at(1),
            severity="info",
            kind="k",
            runner_id="r",
            chunk_id=None,
            lease_id=None,
            node_name=None,
            message="info-old",
            detail=None,
        ),
        EventRow(
            id=2,
            recorded_at=_at(9),
            severity="warning",
            kind="k",
            runner_id="r",
            chunk_id=None,
            lease_id=None,
            node_name=None,
            message="warn-new",
            detail=None,
        ),
        EventRow(
            id=3,
            recorded_at=_at(2),
            severity="critical",
            kind="k",
            runner_id="r",
            chunk_id=None,
            lease_id=None,
            node_name=None,
            message="crit-old",
            detail=None,
        ),
    ]
    escalations = [EscalationOpen(chunk_id="ch_z", recorded_at=_at(8), takeover_command="cd z")]
    feed = EventFeed.of(events, escalations).rows
    # Newest first whatever the severity: warn-new t9, projected needs-human t8, crit-old t2, info-old t1.
    assert [e.message if e.kind == "k" else e.kind for e in feed] == ["warn-new", "needs-human", "crit-old", "info-old"]
    assert [e.severity for e in feed] == ["warning", "critical", "critical", "info"]
    # The projected escalation carries a negative synthetic id.
    projected = next(e for e in feed if e.kind == "needs-human")
    assert projected.id < 0
    # It names its chunk and, naming no runner, carries `runner_id=None` rather than `""`.
    assert projected.chunk_id == "ch_z"
    assert projected.runner_id is None


def test_event_feed_escalation_message_does_not_overclaim_resume() -> None:
    """The feed message points at the escalation without reproducing what it carries —
    neither a runner-composed resume command nor a hub-authored prose field may leak
    into the message; the two-branch wording keys only on whether the raw field is set."""
    runner_composed = EscalationOpen(
        chunk_id="ch_a", recorded_at=_at(1), takeover_command="cd /ws/e1 && claude --resume sess-a"
    )
    hub_authored_empty = EscalationOpen(chunk_id="ch_b", recorded_at=_at(2), takeover_command="")
    hub_authored_prose = EscalationOpen(
        chunk_id="ch_c", recorded_at=_at(3), takeover_command="cross-graph target `x` names no enabled graph — …"
    )

    feed = EventFeed.of([], [runner_composed, hub_authored_empty, hub_authored_prose]).rows
    by_chunk = {e.chunk_id: e.message for e in feed}

    assert by_chunk["ch_a"] == "chunk ch_a needs a human — see the chunk's escalation for how to proceed"
    assert by_chunk["ch_b"] == "chunk ch_b needs a human"
    # The prose shape rides the populated-field branch — same pointer wording, no
    # reproduction of the prose itself.
    assert by_chunk["ch_c"] == "chunk ch_c needs a human — see the chunk's escalation for how to proceed"
    # The regression class that matters: the raw field's content interpolated into
    # the message. These fire on any wording that embeds it.
    assert "claude --resume" not in by_chunk["ch_a"]
    assert "cross-graph" not in by_chunk["ch_c"]


def test_migration_creates_event_log_on_in_place_upgrade(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _HEAD_BEFORE_EVENT_LOG)
    assert not _has_table(engine, "event_log")  # absent before the revision
    runner.upgrade("head")
    # After the in-place upgrade the table exists and is insertable.
    with engine.begin() as conn:
        conn.execute(
            insert(s.event_log).values(
                severity="info",
                kind="k",
                runner_id="r",
                chunk_id=None,
                lease_id=None,
                node_name=None,
                message="m",
                detail=None,
                recorded_at=_T0,
            )
        )
        rows = conn.execute(select(s.event_log)).all()
    assert len(rows) == 1


def _has_table(engine, name: str) -> bool:  # type: ignore[no-untyped-def]
    from sqlalchemy import inspect

    return name in inspect(engine).get_table_names()
