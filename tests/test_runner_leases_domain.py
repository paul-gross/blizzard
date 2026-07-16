"""Derived lease state — ``derive_lease_state`` and ``LocalLeaseService`` (issue #28).

Two tiers: the pure precedence tests (``derive_lease_state``, no store, no I/O) and
the staleness-boundary pin sit at the **unit** tier ([tiers], `blizzard-harness:/
verification/blizzard.md`); ``LocalLeaseService.list_active()`` — wired against a real
tmp sqlite runner store with the fake process probe (``bzh:pluggable-seams``) — sits at
the **component** tier, mirroring ``test_runner_loop.py``'s own store-backed unit tests
and ``test_runner_registry.py``'s component convention.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.domain.leases import (
    HEARTBEAT_STALENESS_THRESHOLD,
    LocalLeaseService,
    derive_lease_state,
    is_heartbeat_stale,
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


# --------------------------------------------------------------------------- #
# derive_lease_state — pure, all five states + precedence
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_derive_lease_state_running_when_alive_and_fresh() -> None:
    lease = _lease_record()
    assert derive_lease_state(lease, is_parked=False, is_alive=True, is_stale=False) == "running"


@pytest.mark.unit
def test_derive_lease_state_stale_when_alive_but_heartbeat_old() -> None:
    lease = _lease_record()
    assert derive_lease_state(lease, is_parked=False, is_alive=True, is_stale=True) == "stale"


@pytest.mark.unit
def test_derive_lease_state_exited_when_process_not_alive() -> None:
    """A dead pid is ADVANCE's exit-is-done, not a stall (D-055) — it derives exited
    even when the (stale) heartbeat check would also fire, since exit is checked first."""
    lease = _lease_record()
    assert derive_lease_state(lease, is_parked=False, is_alive=False, is_stale=True) == "exited"


@pytest.mark.unit
def test_derive_lease_state_spawning_when_pid_unset() -> None:
    lease = _lease_record(pid=None, process_start_time=None)
    assert derive_lease_state(lease, is_parked=False, is_alive=False, is_stale=False) == "spawning"


@pytest.mark.unit
def test_derive_lease_state_spawning_when_session_unset() -> None:
    lease = _lease_record(session_id=None)
    assert derive_lease_state(lease, is_parked=False, is_alive=True, is_stale=False) == "spawning"


@pytest.mark.unit
def test_derive_lease_state_parked_when_dormant_on_a_question() -> None:
    lease = _lease_record()
    assert derive_lease_state(lease, is_parked=True, is_alive=True, is_stale=False) == "parked"


@pytest.mark.unit
def test_derive_lease_state_parked_wins_over_stale() -> None:
    """Precedence: a lease that is both parked and stale derives parked — the reap
    clock is stopped for a dormant lease, so a growing heartbeat age is expected, not
    a stall (design/runner/loop.md, [ask-answer.md])."""
    lease = _lease_record()
    assert derive_lease_state(lease, is_parked=True, is_alive=True, is_stale=True) == "parked"


@pytest.mark.unit
def test_derive_lease_state_spawning_wins_over_an_ancient_heartbeat() -> None:
    """Precedence: a lease with no pid/session derives spawning even when its
    heartbeat baseline reads as stale — the mint→spawn window has no live worker to
    stall yet (D-092)."""
    lease = _lease_record(pid=None, process_start_time=None, session_id=None)
    assert derive_lease_state(lease, is_parked=False, is_alive=False, is_stale=True) == "spawning"


# --------------------------------------------------------------------------- #
# is_heartbeat_stale — the staleness-boundary pin (Phase 1 escalation #2)
# --------------------------------------------------------------------------- #


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
def test_is_heartbeat_stale_at_exact_threshold_is_not_stale(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The true edge: ``now - last == THRESHOLD`` reads not-stale (strict ``>``)."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    at_threshold = _NOW + HEARTBEAT_STALENESS_THRESHOLD

    assert is_heartbeat_stale(store, lease, at_threshold) is False


@pytest.mark.unit
def test_is_heartbeat_stale_just_past_threshold_is_stale(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """One microsecond past the threshold flips stale — pins against a ``>``→``>=`` drift."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    just_past = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(microseconds=1)

    assert is_heartbeat_stale(store, lease, just_past) is True


# --------------------------------------------------------------------------- #
# LocalLeaseService.list_active() — component tier, real sqlite store
# --------------------------------------------------------------------------- #


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
    # comes back UTC-aware, so the domain read model's value already equals what was
    # written — no coercion needed at this call site.
    assert activity.last_heartbeat_at == beat_at


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
