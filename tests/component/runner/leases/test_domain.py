"""Derived lease state — ``LocalLeaseService.list_active()``, wired against a real tmp sqlite store with the fake
process probe (``bzh:pluggable-seams``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.activity import (
    HEARTBEAT_STALENESS_THRESHOLD,
    RECENT_LEASE_LIMIT,
    ClosedLeaseActivity,
    LeaseActivity,
    LocalLeaseService,
)
from blizzard.runner.leases.model import NewLease
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.schema import metadata as runner_metadata
from tests.runner_fakes import FakeProbe, SqlAlchemyRunnerStore, make_store, runner_store_errors

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


# Liveness.stale — the staleness-boundary pin


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_lease(
    store: SqlAlchemyRunnerStore, *, chunk: str = "ch_1", lease: str = "lease_1", created_at: datetime = _NOW
) -> None:
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=created_at,
        )
    )


# LocalLeaseService.list_active() — component tier, real sqlite store


class _CountingParkedIdsStore(SqlAlchemyRunnerStore):
    """The real store, instrumented to count ``parked_lease_ids`` calls (N+1 guard)."""

    def __init__(self, engine: Engine, errors: RunnerStoreErrorFactory) -> None:
        super().__init__(engine, errors)
        self.parked_lease_ids_calls = 0

    def parked_lease_ids(self) -> set[str]:
        self.parked_lease_ids_calls += 1
        return super().parked_lease_ids()


def _lease_service(
    store: SqlAlchemyRunnerStore,
    clock: FixedClock,
    probe: FakeProbe,
    *,
    stale_after: timedelta = HEARTBEAT_STALENESS_THRESHOLD,
    recent_limit: int = RECENT_LEASE_LIMIT,
) -> LocalLeaseService:
    return LocalLeaseService(
        clock,
        probe,
        lease_record=store,
        liveness=store,
        asks=store,
        environments=store,
        overload=store,
        elicitations=store,
        git_commits=store,
        takeovers=store,
        escalations=store,
        stale_after=stale_after,
        recent_limit=recent_limit,
    )


def _counting_store(tmp_path: Path) -> _CountingParkedIdsStore:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'runner.db'}")
    runner_metadata.create_all(engine)
    return _CountingParkedIdsStore(engine, runner_store_errors())


def test_list_active_over_empty_store_returns_empty_list(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = _lease_service(store, FixedClock(_NOW), FakeProbe())

    assert service.list_active() == []


def test_list_active_joins_binding_and_heartbeat(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    beat_at = _NOW + timedelta(minutes=5)
    store.record_heartbeat(lease_id="lease_1", beat_at=beat_at)
    probe = FakeProbe(alive={(100, "start-100")})
    service = _lease_service(store, FixedClock(beat_at), probe)

    activities = service.list_active()

    assert len(activities) == 1
    activity = activities[0]
    assert activity.lease.lease_id == "lease_1"
    assert activity.state == "running"
    assert activity.environment_id == "e1"
    assert activity.workdir == "/ws/e1"
    # The store column is UtcDateTime-typed (``bzh:utc-instants``): a read
    # comes back UTC-aware, no coercion needed at this call site.
    assert activity.last_heartbeat_at == beat_at


def test_list_active_renders_a_just_resumed_lease_running_not_stale(tmp_path: Path) -> None:
    """Reuses REAP's baseline: a lease resumed after a long park does not
    render ``stale``. Its ``last_heartbeat_at`` stays honest — the old beat, not the
    spawn — reporting what the worker last did, not when staleness is measured from."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    resumed_at = _NOW + timedelta(hours=3)
    store.record_spawn(
        "lease_1",
        pid=101,
        process_start_time="start-101",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=resumed_at,
    )
    probe = FakeProbe(alive={(101, "start-101")})
    service = _lease_service(store, FixedClock(resumed_at + timedelta(seconds=30)), probe)

    activities = service.list_active()

    assert [a.state for a in activities] == ["running"]
    assert activities[0].last_heartbeat_at == _NOW


def _seed_spawned_lease(store: SqlAlchemyRunnerStore) -> FakeProbe:
    """One active lease, spawned at ``_NOW``, whose worker process is alive."""
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    return FakeProbe(alive={(100, "start-100")})


def test_list_active_renders_a_lease_running_when_its_heartbeat_is_newer_than_a_spawn_older_than_the_window(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    probe = _seed_spawned_lease(store)
    beat_at = _NOW + timedelta(hours=2)
    store.record_heartbeat(lease_id="lease_1", beat_at=beat_at)
    service = _lease_service(store, FixedClock(beat_at + timedelta(minutes=30)), probe)

    assert [a.state for a in service.list_active()] == ["running"]


def test_list_active_renders_an_alive_lease_stale_when_its_newest_activity_is_older_than_the_window(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    probe = _seed_spawned_lease(store)
    service = _lease_service(store, FixedClock(_NOW + timedelta(hours=2)), probe)

    assert [a.state for a in service.list_active()] == ["stale"]


def test_list_active_honors_a_stale_after_shorter_than_the_default_threshold(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    probe = _seed_spawned_lease(store)
    idle = timedelta(minutes=30)
    assert idle < HEARTBEAT_STALENESS_THRESHOLD
    service = _lease_service(store, FixedClock(_NOW + idle), probe, stale_after=timedelta(minutes=10))

    assert [a.state for a in service.list_active()] == ["stale"]


def test_list_active_reads_parked_lease_ids_once_not_per_lease(tmp_path: Path) -> None:
    store = _counting_store(tmp_path)
    _seed_lease(store, chunk="ch_1", lease="lease_1")
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    _seed_lease(store, chunk="ch_2", lease="lease_2")
    store.record_spawn(
        "lease_2",
        pid=200,
        process_start_time="start-200",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-b"),
        spawned_at=_NOW,
    )
    probe = FakeProbe(alive={(100, "start-100"), (200, "start-200")})
    service = _lease_service(store, FixedClock(_NOW), probe)

    activities = service.list_active()

    assert len(activities) == 2
    assert store.parked_lease_ids_calls == 1


def test_list_active_renders_a_backing_off_lease(tmp_path: Path) -> None:
    """An open, un-elapsed overload fact renders ``backing-off`` — not
    ``exited``, even though the process the fact records has already exited (no probe entry)."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_overload(
        lease_id="lease_1",
        chunk_id="ch_1",
        epoch=1,
        generation=1,
        invocation_kind="worker",
        invocation_identity="1",
        streak_ordinal=1,
        observed_at=_NOW,
        resume_after=_NOW + timedelta(seconds=60),
    )
    service = _lease_service(store, FixedClock(_NOW), FakeProbe())

    activities = service.list_active()

    assert len(activities) == 1
    assert activities[0].state == "backing-off"


# LocalLeaseService.list_recent() — active + recent-closed


def test_list_recent_appends_closed_leases_after_active(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_lease(store, chunk="ch_1", lease="lease_1")
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    _seed_lease(store, chunk="ch_2", lease="lease_2")
    store.record_spawn(
        "lease_2",
        pid=200,
        process_start_time="start-200",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-b"),
        spawned_at=_NOW,
    )
    closed_at = _NOW + timedelta(minutes=5)
    store.record_closure(
        lease_id="lease_2", chunk_id="ch_2", node_id="nd_build", reason="transitioned", closed_at=closed_at
    )
    probe = FakeProbe(alive={(100, "start-100")})
    service = _lease_service(store, FixedClock(_NOW), probe)

    activities = service.list_recent()

    assert [a.lease.lease_id for a in activities] == ["lease_1", "lease_2"]
    active, closed = activities
    assert isinstance(active, LeaseActivity)
    assert active.state == "running"
    assert isinstance(closed, ClosedLeaseActivity)
    assert closed.state == "closed"
    assert closed.closed_at == closed_at
    assert closed.closure_reason == "transitioned"


def test_list_recent_active_lease_not_crowded_out_by_newer_closed_leases(tmp_path: Path) -> None:
    """A single newest-N-overall read would let recently-closed leases crowd out a
    long-running active one. Pinned here with ``recent_limit=1`` — the active lease is
    older than both closed ones and must still appear."""
    store = _store(tmp_path)
    old_active_at = _NOW - timedelta(hours=2)
    _seed_lease(store, chunk="ch_active", lease="lease_active", created_at=old_active_at)
    store.record_spawn(
        "lease_active",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-active"),
        spawned_at=old_active_at,
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
    service = _lease_service(store, FixedClock(_NOW), probe, recent_limit=1)

    activities = service.list_recent()

    assert [a.lease.lease_id for a in activities] == ["lease_active", "lease_closed_2"]


def test_list_recent_closed_activity_carries_no_environment_binding(tmp_path: Path) -> None:
    """A closed lease's bindings are always released by the time closure is recorded
     — the read model must be honest about that, so ``environment_id``/
    ``workdir`` come back ``None`` even though a binding once existed."""
    store = _store(tmp_path)
    _seed_lease(store, chunk="ch_1", lease="lease_1")
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    service = _lease_service(store, FixedClock(_NOW), FakeProbe())

    activities = service.list_recent()

    assert len(activities) == 1
    assert isinstance(activities[0], ClosedLeaseActivity)
    assert not hasattr(activities[0], "environment_id")
    assert not hasattr(activities[0], "workdir")
