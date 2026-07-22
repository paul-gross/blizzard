"""The operational event log — store writes/reads, the unified-feed derivation, and the
migration (issue #125, Phase 1, unit tier).

``event_log`` is an append-only operational-fact table (``bzh:facts-not-status``): one
row per operationally-significant thing that happened, clock-stamped by the caller, no
status column. These tests pin, in isolation: a row round-trips its columns and its JSON
``detail``; a runner-scoped event carries no ``chunk_id``; ``list_events`` filters and
orders newest-first, bounded; ``list_open_escalations`` applies the same supersession rule
``open_escalation`` does, fleet-wide; ``derive_event_feed`` unifies the two
severity-then-recency; and the migration creates the table on a fresh ``base -> head`` and
an in-place upgrade alike.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.work import EscalationOpen, EventRow, derive_event_feed
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_store import ChunkStore
from tests.support import migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_HEAD_BEFORE_EVENT_LOG = "20260721_1500_hub_cli_auth_state_user"


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _store(tmp_path: Path) -> ChunkStore:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_a", graph_id="gr_1", at=_T0)
        seed_chunk(conn, "ch_b", graph_id="gr_1", at=_T0)
    return ChunkStore(engine, FixedClock(_T0))


def test_record_event_roundtrips_columns_and_json_detail(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_event(
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
    (row,) = store.list_events()
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
    store = _store(tmp_path)
    store.record_event(
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
    (row,) = store.list_events()
    assert row.chunk_id is None
    assert row.lease_id is None
    assert row.detail is None


def test_list_events_filters_and_orders_newest_first_bounded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_event(
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
    store.record_event(
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
    store.record_event(
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
    assert [e.message for e in store.list_events()] == ["c", "b", "a"]
    # Filters.
    assert [e.message for e in store.list_events(severity="warning")] == ["b"]
    assert [e.message for e in store.list_events(runner_id="r2")] == ["c"]
    assert [e.message for e in store.list_events(chunk_id="ch_a")] == ["b", "a"]
    assert [e.message for e in store.list_events(since=_at(2))] == ["c", "b"]
    # Bounded.
    assert [e.message for e in store.list_events(limit=1)] == ["c"]


def test_list_open_escalations_applies_supersession_fleet_wide(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with store._engine.begin() as conn:  # seed a third chunk for the requeue case
        seed_chunk(conn, "ch_c", graph_id="gr_1", at=_T0)

    # ch_a: escalation, nothing after it -> OPEN.
    store.record_escalation("ch_a", epoch=1, takeover_command="cd a && resume", at=_at(10))
    # ch_b: escalation then a LATER lease mint -> superseded (closed).
    store.record_escalation("ch_b", epoch=1, takeover_command="cd b && resume", at=_at(10))
    store.record_lease("ch_b", epoch=2, runner_id="r1", at=_at(20))
    # ch_c: escalation then a LATER requeue -> superseded (closed).
    store.record_escalation("ch_c", epoch=1, takeover_command="cd c && resume", at=_at(10))
    store.record_requeue("ch_c", at=_at(20))

    opens = store.list_open_escalations()
    assert [e.chunk_id for e in opens] == ["ch_a"]
    assert opens[0].takeover_command == "cd a && resume"


def test_derive_event_feed_sorts_severity_then_recency() -> None:
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
    escalations = [EscalationOpen(chunk_id="ch_z", epoch=1, recorded_at=_at(8), takeover_command="cd z")]
    feed = derive_event_feed(events, escalations)
    # critical band first (crit-old at t2, then projected needs-human at t8 — but newest-first
    # within band => needs-human t8 before crit-old t2), then warning, then info.
    assert [e.message.split()[0] if e.kind == "k" else e.kind for e in feed][0:2] == ["needs-human", "crit-old"]
    assert [e.severity for e in feed] == ["critical", "critical", "warning", "info"]
    # The projected escalation carries a negative synthetic id.
    projected = next(e for e in feed if e.kind == "needs-human")
    assert projected.id < 0
    assert projected.chunk_id == "ch_z"


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
