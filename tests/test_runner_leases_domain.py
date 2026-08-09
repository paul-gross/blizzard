"""Derived lease state — ``LeaseActivity.state`` and ``LocalLeaseService`` (issue #28).

Two tiers: the pure precedence tests (no store, no I/O) sit at unit; ``LocalLeaseService
.list_active()`` — wired against a real tmp sqlite store with the fake process probe
(``bzh:pluggable-seams``) — sits at component, mirroring ``test_runner_loop.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.domain.leases import (
    HEARTBEAT_STALENESS_THRESHOLD,
    LeaseActivity,
    Liveness,
    LocalLeaseService,
)
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import LeaseRecord, NewLease
from blizzard.runner.store.schema import metadata as runner_metadata
from tests.runner_fakes import FakeProbe, make_store

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _lease_record(**overrides: object) -> LeaseRecord:
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "runner_id": "r1",
        "retries_max": 2,
        "created_at": _NOW,
        "pid": 100,
        "process_start_time": "start-100",
        "session_id": "sess-a",
    }
    fields.update(overrides)
    return LeaseRecord(**fields)  # type: ignore[arg-type]


# LeaseActivity.state — pure, all six states + precedence


@pytest.mark.unit
def test_state_running_when_alive_and_fresh() -> None:
    lease = _lease_record()
    assert LeaseActivity(lease, closed=False, parked=False, alive=True, stale=False).state == "running"


@pytest.mark.unit
def test_state_stale_when_alive_but_heartbeat_old() -> None:
    lease = _lease_record()
    assert LeaseActivity(lease, closed=False, parked=False, alive=True, stale=True).state == "stale"


@pytest.mark.unit
def test_state_exited_when_process_not_alive() -> None:
    """A dead pid is ADVANCE's exit-is-done, not a stall — it derives exited
    even when the (stale) heartbeat check would also fire, since exit is checked first."""
    lease = _lease_record()
    assert LeaseActivity(lease, closed=False, parked=False, alive=False, stale=True).state == "exited"


@pytest.mark.unit
def test_state_spawning_when_pid_unset() -> None:
    lease = _lease_record(pid=None, process_start_time=None)
    assert LeaseActivity(lease, closed=False, parked=False, alive=False, stale=False).state == "spawning"


@pytest.mark.unit
def test_state_spawning_when_session_unset() -> None:
    lease = _lease_record(session_id=None)
    assert LeaseActivity(lease, closed=False, parked=False, alive=True, stale=False).state == "spawning"


@pytest.mark.unit
def test_state_parked_when_dormant_on_a_question() -> None:
    lease = _lease_record()
    assert LeaseActivity(lease, closed=False, parked=True, alive=True, stale=False).state == "parked"


@pytest.mark.unit
def test_state_parked_wins_over_stale() -> None:
    """Precedence: a lease that is both parked and stale derives parked — the reap
    clock is stopped for a dormant lease, so a growing heartbeat age is expected, not
    a stall."""
    lease = _lease_record()
    assert LeaseActivity(lease, closed=False, parked=True, alive=True, stale=True).state == "parked"


@pytest.mark.unit
def test_state_spawning_wins_over_an_ancient_heartbeat() -> None:
    """Precedence: a lease with no pid/session derives spawning even when its
    heartbeat baseline reads as stale — the mint→spawn window has no live worker to
    stall yet."""
    lease = _lease_record(pid=None, process_start_time=None, session_id=None)
    assert LeaseActivity(lease, closed=False, parked=False, alive=False, stale=True).state == "spawning"


@pytest.mark.unit
def test_state_closed_when_closure_fact_exists() -> None:
    """Closed outranks exited: the process is gone, which derives ``exited`` on its own, and
    the closure fact still wins. The pid-reuse case below is the same precedence against a
    *live* pid."""
    lease = _lease_record()
    assert LeaseActivity(lease, closed=True, parked=False, alive=False, stale=False).state == "closed"


@pytest.mark.unit
def test_state_closed_wins_over_alive_pid_reuse() -> None:
    """Precedence: a closed lease's pid may have been reused by an unrelated
    process, so ``is_alive=True`` can be a false positive. Closure is the terminal
    fact and must win, or a finished agent would misread as still ``running``."""
    lease = _lease_record()
    assert LeaseActivity(lease, closed=True, parked=False, alive=True, stale=False).state == "closed"


@pytest.mark.unit
def test_state_closed_wins_over_stale() -> None:
    lease = _lease_record()
    assert LeaseActivity(lease, closed=True, parked=False, alive=True, stale=True).state == "closed"


@pytest.mark.unit
def test_state_closed_wins_over_parked() -> None:
    """Precedence: closed outranks even parked — the highest-precedence live state — the
    same way parked outranks stale."""
    lease = _lease_record()
    assert LeaseActivity(lease, closed=True, parked=True, alive=True, stale=False).state == "closed"


# Liveness.stale — the staleness-boundary pin (Phase 1 escalation #2)


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_lease(store, *, chunk="ch_1", lease="lease_1", created_at=_NOW) -> None:  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=created_at,
        )
    )


@pytest.mark.unit
def test_stale_at_exact_threshold_is_not_stale(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The true edge: ``now - last == THRESHOLD`` reads not-stale (strict ``>``)."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    at_threshold = _NOW + HEARTBEAT_STALENESS_THRESHOLD

    assert Liveness.of(store, lease).stale(at_threshold) is False


@pytest.mark.unit
def test_stale_just_past_threshold_is_stale(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """One microsecond past the threshold flips stale — pins against a ``>``→``>=`` drift."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    just_past = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(microseconds=1)

    assert Liveness.of(store, lease).stale(just_past) is True


@pytest.mark.unit
def test_a_worker_resumed_after_a_long_park_gets_the_full_staleness_window(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Issue #150's live incident, at the predicate: an ask-park resumed at T+2h, far
    past the threshold, records a fresh ``lease_spawns`` row — the worker gets the
    whole window back, not stale at birth and reaped seconds into its first turn."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_spawn("lease_1", pid=1, process_start_time="s1", session_id="sess", spawned_at=_NOW)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None

    # Parked for two hours: without the spawn fact in the baseline the lease is long stale.
    resumed_at = _NOW + timedelta(hours=2)
    assert Liveness.of(store, lease).stale(resumed_at) is True

    # The answer-resume respawns the same lease — a second generation, same lease_id.
    store.record_spawn("lease_1", pid=2, process_start_time="s2", session_id="sess", spawned_at=resumed_at)

    assert Liveness.of(store, lease).stale(resumed_at + timedelta(seconds=1)) is False
    assert Liveness.of(store, lease).stale(resumed_at + HEARTBEAT_STALENESS_THRESHOLD) is False
    assert (
        Liveness.of(store, lease).stale(resumed_at + HEARTBEAT_STALENESS_THRESHOLD + timedelta(seconds=1)) is True
    )  # and the window still closes, absent new heartbeats


@pytest.mark.unit
def test_a_heartbeat_newer_than_the_spawn_still_sets_the_baseline(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The baseline is the newest of the two, not the spawn alone: a worker beating away
    long after its spawn must not be measured from the spawn and reaped mid-work."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_spawn("lease_1", pid=1, process_start_time="s1", session_id="sess", spawned_at=_NOW)
    beat_at = _NOW + timedelta(minutes=50)
    store.record_heartbeat(lease_id="lease_1", beat_at=beat_at)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None

    assert Liveness.of(store, lease).stale(_NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(minutes=1)) is False
    assert Liveness.of(store, lease).stale(beat_at + HEARTBEAT_STALENESS_THRESHOLD + timedelta(seconds=1)) is True


@pytest.mark.unit
def test_a_lease_with_neither_a_beat_nor_a_spawn_falls_back_to_its_mint(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """REAP's residue — minted at FILL, spawn-return never recorded. The pre-#150
    ``created_at`` floor is unchanged for it."""
    store = _store(tmp_path)
    _seed_lease(store)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None

    assert Liveness.of(store, lease).stale(_NOW + HEARTBEAT_STALENESS_THRESHOLD) is False
    assert Liveness.of(store, lease).stale(_NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(seconds=1)) is True


# LocalLeaseService.list_active() — component tier, real sqlite store


class _CountingParkedIdsStore(SqlAlchemyRunnerStore):
    """The real store, instrumented to count ``parked_lease_ids`` calls (N+1 guard)."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine)
        self.parked_lease_ids_calls = 0

    def parked_lease_ids(self) -> set[str]:
        self.parked_lease_ids_calls += 1
        return super().parked_lease_ids()


def _counting_store(tmp_path) -> _CountingParkedIdsStore:  # type: ignore[no-untyped-def]
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'runner.db'}")
    runner_metadata.create_all(engine)
    return _CountingParkedIdsStore(engine)


@pytest.mark.component
def test_list_active_over_empty_store_returns_empty_list(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    service = LocalLeaseService(store, FixedClock(_NOW), FakeProbe())

    assert service.list_active() == []


@pytest.mark.component
def test_list_active_joins_binding_and_heartbeat(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    beat_at = _NOW + timedelta(minutes=5)
    store.record_heartbeat(lease_id="lease_1", beat_at=beat_at)
    probe = FakeProbe(alive={(100, "start-100")})
    service = LocalLeaseService(store, FixedClock(beat_at), probe)

    activities = service.list_active()

    assert len(activities) == 1
    activity = activities[0]
    assert activity.lease.lease_id == "lease_1"
    assert activity.state == "running"
    assert activity.environment_id == "e1"
    assert activity.workdir == "/ws/e1"
    # The store column is UtcDateTime-typed (issue #28, ``bzh:utc-instants``): a read
    # comes back UTC-aware, no coercion needed at this call site.
    assert activity.last_heartbeat_at == beat_at


@pytest.mark.component
def test_list_active_renders_a_just_resumed_lease_running_not_stale(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Reuses REAP's baseline (issue #150): a lease resumed after a long park does not
    render ``stale``. Its ``last_heartbeat_at`` stays honest — the old beat, not the
    spawn — reporting what the worker last did, not when staleness is measured from."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    resumed_at = _NOW + timedelta(hours=3)
    store.record_spawn("lease_1", pid=101, process_start_time="start-101", session_id="sess-a", spawned_at=resumed_at)
    probe = FakeProbe(alive={(101, "start-101")})
    service = LocalLeaseService(store, FixedClock(resumed_at + timedelta(seconds=30)), probe)

    activities = service.list_active()

    assert [a.state for a in activities] == ["running"]
    assert activities[0].last_heartbeat_at == _NOW


@pytest.mark.component
def test_list_active_reads_parked_lease_ids_once_not_per_lease(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _counting_store(tmp_path)
    _seed_lease(store, chunk="ch_1", lease="lease_1")
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    _seed_lease(store, chunk="ch_2", lease="lease_2")
    store.record_spawn("lease_2", pid=200, process_start_time="start-200", session_id="sess-b", spawned_at=_NOW)
    probe = FakeProbe(alive={(100, "start-100"), (200, "start-200")})
    service = LocalLeaseService(store, FixedClock(_NOW), probe)

    activities = service.list_active()

    assert len(activities) == 2
    assert store.parked_lease_ids_calls == 1


# LocalLeaseService.list_recent() — active + recent-closed (issue #29)


@pytest.mark.component
def test_list_recent_appends_closed_leases_after_active(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store, chunk="ch_1", lease="lease_1")
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    _seed_lease(store, chunk="ch_2", lease="lease_2")
    store.record_spawn("lease_2", pid=200, process_start_time="start-200", session_id="sess-b", spawned_at=_NOW)
    closed_at = _NOW + timedelta(minutes=5)
    store.record_closure(
        lease_id="lease_2", chunk_id="ch_2", node_id="nd_build", reason="transitioned", closed_at=closed_at
    )
    probe = FakeProbe(alive={(100, "start-100")})
    service = LocalLeaseService(store, FixedClock(_NOW), probe)

    activities = service.list_recent()

    assert [a.lease.lease_id for a in activities] == ["lease_1", "lease_2"]
    active, closed = activities
    assert active.state == "running"
    assert active.closed_at is None
    assert active.closure_reason is None
    assert closed.state == "closed"
    assert closed.closed_at == closed_at
    assert closed.closure_reason == "transitioned"


@pytest.mark.component
def test_list_recent_active_lease_not_crowded_out_by_newer_closed_leases(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A single newest-N-overall read would let recently-closed leases crowd out a
    long-running active one. Pinned here with ``recent_limit=1`` — the active lease is
    older than both closed ones and must still appear."""
    store = _store(tmp_path)
    old_active_at = _NOW - timedelta(hours=2)
    _seed_lease(store, chunk="ch_active", lease="lease_active", created_at=old_active_at)
    store.record_spawn(
        "lease_active", pid=100, process_start_time="start-100", session_id="sess-active", spawned_at=old_active_at
    )
    _seed_lease(store, chunk="ch_closed_1", lease="lease_closed_1", created_at=_NOW)
    store.record_closure(
        lease_id="lease_closed_1", chunk_id="ch_closed_1", node_id="nd_build", reason="failed", closed_at=_NOW
    )
    _seed_lease(store, chunk="ch_closed_2", lease="lease_closed_2", created_at=_NOW)
    newer_closed_at = _NOW + timedelta(minutes=1)
    store.record_closure(
        lease_id="lease_closed_2",
        chunk_id="ch_closed_2",
        node_id="nd_build",
        reason="failed",
        closed_at=newer_closed_at,
    )
    probe = FakeProbe(alive={(100, "start-100")})
    service = LocalLeaseService(store, FixedClock(_NOW), probe, recent_limit=1)

    activities = service.list_recent()

    assert [a.lease.lease_id for a in activities] == ["lease_active", "lease_closed_2"]


@pytest.mark.component
def test_list_recent_closed_activity_carries_no_environment_binding(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A closed lease's bindings are always released by the time closure is recorded
    (issue #29) — the read model must be honest about that, so ``environment_id``/
    ``workdir`` come back ``None`` even though a binding once existed."""
    store = _store(tmp_path)
    _seed_lease(store, chunk="ch_1", lease="lease_1")
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    service = LocalLeaseService(store, FixedClock(_NOW), FakeProbe())

    activities = service.list_recent()

    assert len(activities) == 1
    assert activities[0].environment_id is None
    assert activities[0].workdir is None


@pytest.mark.unit
def test_liveness_uses_a_supplied_heartbeat_without_re_reading_it(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The caller may already have read `latest_heartbeat`, so it can hand it in
    rather than have `Liveness.of` re-query. The sentinel keeps a supplied `None`
    (a lease that genuinely never beat) distinct from "not supplied, go read it"."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_spawn("lease_1", pid=1, process_start_time="s1", session_id="sess", spawned_at=_NOW)
    beat_at = _NOW + timedelta(minutes=30)
    store.record_heartbeat(lease_id="lease_1", beat_at=beat_at)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None

    assert Liveness.of(store, lease).last_activity == beat_at  # unsupplied: reads it itself
    assert Liveness.of(store, lease, heartbeat=beat_at).last_activity == beat_at  # supplied: same answer
    # A supplied None means "never beat" and must not be re-read into the real value —
    # the baseline falls back to the spawn, not to the heartbeat sitting in the store.
    assert Liveness.of(store, lease, heartbeat=None).last_activity == _NOW
