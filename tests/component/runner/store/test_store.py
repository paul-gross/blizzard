"""Runner-store repository — the facts-only derivations (``bzh:facts-not-status``).

Active = no closure, held = no release, tenure = any unreleased binding. These
assert the SQL derivations the loop relies on, against a real tmp sqlite store.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import sqlalchemy as sa

from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.store.schema import external_usage_samples, heartbeats
from tests.runner_fakes import FIXTURE_RUNNER_ID, SqlAlchemyRunnerStore
from tests.support_runner_store import (
    _NOW,  # pyright: ignore[reportPrivateUsage]
    _mint,  # pyright: ignore[reportPrivateUsage]
    _mint_with_session,  # pyright: ignore[reportPrivateUsage]
    _session_id,  # pyright: ignore[reportPrivateUsage]
    _store,  # pyright: ignore[reportPrivateUsage]
)


def test_lease_spans_closure_where_active_lease_does_not(tmp_path: Path):
    """``lease()`` is the closure-spanning read ``active_lease()`` is *not*
    — a transcript outlives its lease, so the read that serves it must too."""
    store = _store(tmp_path)
    _mint(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)

    assert store.active_lease("lease_1") is None
    closed = store.lease("lease_1")
    assert closed is not None
    assert closed.lease_id == "lease_1"


def test_lease_returns_none_for_an_unknown_id(tmp_path: Path):
    store = _store(tmp_path)
    assert store.lease("no-such-lease") is None


def test_latest_session_returns_most_recent_session_bearing_lease(tmp_path: Path):
    """Node-entry resume resolution: ``node_name=None`` spans every
    node of the chunk, newest-first by mint order."""
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", node="nd_build", node_name="build", lease="lease_1", epoch=1)
    store.record_spawn(
        "lease_1",
        pid=1,
        process_start_time="1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-build-1"),
        spawned_at=_NOW,
    )

    store.record_lease(
        NewLease(
            lease_id="lease_2",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_review",
            node_name="review",
            epoch=2,
            retries_max=2,
            created_at=_NOW + timedelta(minutes=5),
        )
    )
    store.record_spawn(
        "lease_2",
        pid=2,
        process_start_time="2",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-review-1"),
        spawned_at=_NOW + timedelta(minutes=5),
    )

    assert _session_id(store, "ch_1", None) == "sess-review-1"
    assert _session_id(store, "ch_1", "build") == "sess-build-1"
    assert _session_id(store, "ch_1", "review") == "sess-review-1"


def test_a_released_closure_discards_every_earlier_session_from_both_lookups(tmp_path: Path):
    store = _store(tmp_path)
    _mint_with_session(store, lease="lease_1", session="sess-1", epoch=1, at=_NOW, session_name="pool")
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    _mint_with_session(
        store, lease="lease_2", session="sess-2", epoch=2, at=_NOW + timedelta(minutes=1), session_name="pool"
    )
    store.record_closure(
        lease_id="lease_2",
        chunk_id="ch_1",
        node_id="nd_build",
        reason="released",
        closed_at=_NOW + timedelta(minutes=2),
    )

    # The older `transitioned` session must not surface in the released one's place.
    assert store.latest_session("ch_1", None) is None
    assert store.latest_session("ch_1", "build") is None
    assert store.pool_head("ch_1", "pool") is None

    _mint_with_session(
        store, lease="lease_3", session="sess-3", epoch=3, at=_NOW + timedelta(minutes=3), session_name="pool"
    )
    assert _session_id(store, "ch_1", None) == "sess-3"
    head = store.pool_head("ch_1", "pool")
    assert head is not None and head.session_id == "sess-3"


def test_without_a_released_closure_an_earlier_transitioned_session_is_still_returned(tmp_path: Path):
    store = _store(tmp_path)
    _mint_with_session(store, lease="lease_1", session="sess-1", epoch=1, at=_NOW, session_name="pool")
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)

    assert _session_id(store, "ch_1", None) == "sess-1"
    head = store.pool_head("ch_1", "pool")
    assert head is not None and head.session_id == "sess-1"


def test_latest_session_returns_none_when_no_session_or_no_match(tmp_path: Path):
    store = _store(tmp_path)
    assert store.latest_session("ch_none", None) is None

    _mint(store, chunk="ch_1", node="nd_build", node_name="build", lease="lease_1")
    # Lease minted but never spawned — no session_id yet.
    assert store.latest_session("ch_1", None) is None
    assert store.latest_session("ch_1", "build") is None
    assert store.latest_session("ch_1", "review") is None


def test_latest_session_breaks_created_at_ties_by_lease_id(tmp_path: Path):
    """``created_at`` is not a total order — tied timestamps must still resolve
    deterministically, by the monotonic ``lease_id`` (bzh:sql-portable)."""
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", node="nd_build", node_name="build", lease="lease_1")
    store.record_spawn(
        "lease_1",
        pid=1,
        process_start_time="1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1"),
        spawned_at=_NOW,
    )

    store.record_lease(
        NewLease(
            lease_id="lease_2",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=2,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_2",
        pid=2,
        process_start_time="2",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-2"),
        spawned_at=_NOW,
    )

    assert _session_id(store, "ch_1", None) == "sess-2"
    assert _session_id(store, "ch_1", "build") == "sess-2"


def test_list_closed_leases_orders_newest_first_and_respects_limit(tmp_path: Path):
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", lease="lease_1")
    _mint(store, chunk="ch_2", lease="lease_2")
    _mint(store, chunk="ch_3", lease="lease_3")
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    store.record_closure(
        lease_id="lease_2",
        chunk_id="ch_2",
        node_id="nd_build",
        reason="failed",
        closed_at=_NOW + timedelta(minutes=5),
    )
    store.record_closure(
        lease_id="lease_3",
        chunk_id="ch_3",
        node_id="nd_build",
        reason="escalated",
        closed_at=_NOW + timedelta(minutes=10),
    )

    closed = store.list_closed_leases(limit=20)
    assert [c.lease.lease_id for c in closed] == ["lease_3", "lease_2", "lease_1"]
    assert closed[0].reason == "escalated"
    assert closed[0].closed_at == _NOW + timedelta(minutes=10)

    limited = store.list_closed_leases(limit=2)
    assert [c.lease.lease_id for c in limited] == ["lease_3", "lease_2"]


def test_list_closed_leases_excludes_active_leases(tmp_path: Path):
    store = _store(tmp_path)
    _mint(store, chunk="ch_1", lease="lease_1")
    _mint(store, chunk="ch_2", lease="lease_2")
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="reaped", closed_at=_NOW)

    assert [c.lease.lease_id for c in store.list_closed_leases(limit=20)] == ["lease_1"]


def _outbound_row_seqs(tmp_path: Path) -> set[int]:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    with engine.connect() as conn:
        from blizzard.runner.store.schema import outbound_buffer

        return set(
            conn.execute(sa.select(outbound_buffer.c.seq).where(outbound_buffer.c.runner_id == FIXTURE_RUNNER_ID))
            .scalars()
            .all()
        )


def test_prune_outbound_deletes_old_acked_rows_below_the_pending_floor(tmp_path: Path):
    """Retention: an acked row past the window is pruned only
    when its seq sits below the lowest still-pending seq — one interleaved above it
    survives regardless of age, keeping the retained buffer gapless from the floor up."""
    store = _store(tmp_path)
    old = _NOW - timedelta(days=8)  # past the 7-day window
    below_floor = store.enqueue_outbound(
        kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=old
    )
    pending = store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=old)
    above_floor = store.enqueue_outbound(
        kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=old
    )
    store.ack_outbound(below_floor, acked_at=old)
    store.ack_outbound(above_floor, acked_at=old)  # acked, old, but ABOVE the pending floor

    pruned = store.prune_outbound(now=_NOW)

    assert pruned == 1
    assert _outbound_row_seqs(tmp_path) == {pending, above_floor}
    assert [f.seq for f in store.pending_outbound()] == [pending]


def test_prune_outbound_keeps_acked_rows_still_inside_the_retention_window(tmp_path: Path):
    """An acked row younger than the retention window survives even below the pending
    floor — retention is about age, not merely about being superseded by a pending row."""
    store = _store(tmp_path)
    recent = _NOW - timedelta(days=1)
    acked = store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=recent)
    pending = store.enqueue_outbound(
        kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=recent
    )
    store.ack_outbound(acked, acked_at=recent)

    pruned = store.prune_outbound(now=_NOW)

    assert pruned == 0
    assert _outbound_row_seqs(tmp_path) == {acked, pending}


def test_prune_heartbeats_removes_a_superseded_beat_but_never_the_newest(tmp_path: Path):
    """A lease's newest beat survives regardless of age — ``latest_heartbeat`` answers
    identically before and after — while a superseded beat past the window is pruned."""
    store = _store(tmp_path)
    superseded = _NOW - timedelta(days=2)  # past the 1-day window
    newest = superseded + timedelta(minutes=1)  # also past the window, but the newest
    store.record_heartbeat(lease_id="lease_1", beat_at=superseded)
    store.record_heartbeat(lease_id="lease_1", beat_at=newest)
    before = store.latest_heartbeat("lease_1")

    pruned = store.prune_heartbeats(now=_NOW)

    assert pruned == 1
    assert store.latest_heartbeat("lease_1") == before == newest
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    with engine.connect() as conn:
        remaining = (
            conn.execute(
                sa.select(heartbeats.c.beat_at).where(
                    heartbeats.c.lease_id == "lease_1", heartbeats.c.runner_id == FIXTURE_RUNNER_ID
                )
            )
            .scalars()
            .all()
        )
    assert list(remaining) == [newest]


def test_prune_heartbeats_never_prunes_a_same_instant_tied_newest_pair(tmp_path: Path):
    """``< latest_beat``, never ``<=``: two beats tied for newest are both spared, even
    when that tied instant is itself past the retention window."""
    store = _store(tmp_path)
    tied = _NOW - timedelta(days=2)  # past the window
    store.record_heartbeat(lease_id="lease_1", beat_at=tied)
    store.record_heartbeat(lease_id="lease_1", beat_at=tied)

    pruned = store.prune_heartbeats(now=_NOW)

    assert pruned == 0
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    with engine.connect() as conn:
        remaining = conn.execute(
            sa.select(sa.func.count())
            .select_from(heartbeats)
            .where(heartbeats.c.lease_id == "lease_1", heartbeats.c.runner_id == FIXTURE_RUNNER_ID)
        ).scalar_one()
    assert remaining == 2


def _record_external_usage_attempt(store: SqlAlchemyRunnerStore, *, slug: str, sampled_at: datetime) -> None:
    store.record_external_usage_attempt(
        slug=slug, sampled_at=sampled_at, payload=None, report_kind="", report_payload=""
    )


def test_prune_external_usage_samples_removes_a_superseded_attempt_but_never_the_newest(tmp_path: Path):
    """A slug's newest attempt survives regardless of age — ``last_external_usage_attempt_at``
    answers identically before and after — while a superseded attempt past the window is pruned."""
    store = _store(tmp_path)
    superseded = _NOW - timedelta(days=2)  # past the 1-day window
    newest = superseded + timedelta(minutes=1)  # also past the window, but the newest
    _record_external_usage_attempt(store, slug="anthropic", sampled_at=superseded)
    _record_external_usage_attempt(store, slug="anthropic", sampled_at=newest)
    before = store.last_external_usage_attempt_at("anthropic")

    pruned = store.prune_external_usage_samples(now=_NOW)

    assert pruned == 1
    assert store.last_external_usage_attempt_at("anthropic") == before == newest
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    with engine.connect() as conn:
        remaining = (
            conn.execute(
                sa.select(external_usage_samples.c.sampled_at).where(external_usage_samples.c.slug == "anthropic")
            )
            .scalars()
            .all()
        )
    assert list(remaining) == [newest]


def test_prune_external_usage_samples_never_prunes_a_same_instant_tied_newest_pair(tmp_path: Path):
    """``< latest_sample``, never ``<=``: two attempts tied for newest are both spared,
    even when that tied instant is itself past the retention window."""
    store = _store(tmp_path)
    tied = _NOW - timedelta(days=2)  # past the window
    _record_external_usage_attempt(store, slug="anthropic", sampled_at=tied)
    _record_external_usage_attempt(store, slug="anthropic", sampled_at=tied)

    pruned = store.prune_external_usage_samples(now=_NOW)

    assert pruned == 0
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    with engine.connect() as conn:
        remaining = conn.execute(
            sa.select(sa.func.count())
            .select_from(external_usage_samples)
            .where(external_usage_samples.c.slug == "anthropic")
        ).scalar_one()
    assert remaining == 2
