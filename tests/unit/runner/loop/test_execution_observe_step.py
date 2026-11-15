"""``ExecutionObserve`` — the tick's harness execution-evidence lane, and what REAP reads of it.

Driven through a :class:`FakeHarness` whose scripted observation each test sets, under a
:class:`FixedClock` the test advances a minute at a time, as the tick would."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.observation import (
    ExecutionActivity,
    ExecutionLiveness,
    ExecutionObservation,
    ExecutionObservationRequest,
)
from blizzard.runner.leases.activity import HEARTBEAT_STALENESS_THRESHOLD, NO_PROGRESS_THRESHOLD
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.steps import ExecutionObserve, Reap
from blizzard.runner.node_steps.envelope import Envelope
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    make_store,
)
from tests.support.runner_mint import new_mint
from tests.support.takeover_unit import record_takeover_in_unit

_NOW = datetime(2026, 11, 14, 9, 0, 0, tzinfo=UTC)
_SESSION = "sess-a"
_ALIVE = (100, "start-100")
_HANDLE = WorkerHandle(session_id="sess-next", pid=999, process_start_time="start-999", pgid=999)
_MINUTE = timedelta(minutes=1)


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _envelope() -> Envelope:
    return make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok"), ("fail", "no")])


def _seed(store: SqlAlchemyRunnerStore, *, spawned: bool = True) -> None:
    """One build lease, minted at ``_NOW`` and — unless ``spawned`` is false — spawned then too."""
    store.record_mint(
        new_mint(
            NewLease(
                lease_id="lease_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=_NOW,
            )
        )
    )
    if spawned:
        _spawn(store, at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")


def _spawn(store: SqlAlchemyRunnerStore, *, at: datetime) -> None:
    store.record_spawn(
        "lease_1",
        pid=_ALIVE[0],
        process_start_time=_ALIVE[1],
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION),
        spawned_at=at,
    )


class _RaisingHarness(FakeHarness):
    """A harness whose observation query fails outright."""

    def observe_execution(self, request: ExecutionObservationRequest) -> ExecutionObservation:
        raise RuntimeError("transport down")


def _ctx(
    store: SqlAlchemyRunnerStore, clock: FixedClock, *, alive: bool = True, harness: FakeHarness | None = None
) -> tuple[LoopContext, FakeHarness]:
    hub = FakeHub()
    hub.envelopes["ch_1"] = _envelope()
    harness = harness if harness is not None else FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(alive={_ALIVE} if alive else set()),
        clock=clock,
    )
    return ctx, harness


def _alive(at: datetime, *, progress: datetime | None = None) -> ExecutionObservation:
    return ExecutionObservation(
        liveness=ExecutionLiveness.ALIVE, activity=ExecutionActivity.TOOL, evidence_at=at, progress_at=progress
    )


def _still_held(store: SqlAlchemyRunnerStore) -> bool:
    lease = store.active_lease_for_chunk("ch_1")
    return lease is not None and lease.lease_id == "lease_1"


def _warnings(store: SqlAlchemyRunnerStore) -> list[dict[str, Any]]:
    payloads = [json.loads(f.payload) for f in store.pending_outbound() if f.kind == EVENT_RECORDED]
    return [p for p in payloads if p["kind"] == "worker-no-progress"]


def _minutes(
    ctx: LoopContext,
    harness: FakeHarness,
    clock: FixedClock,
    store: SqlAlchemyRunnerStore,
    count: int,
    *,
    answer: str = "alive",
) -> None:
    """``count`` ticks a minute apart, each observing then reaping, as the tick orders them across ticks."""
    for _ in range(count):
        clock.advance(_MINUTE)
        now = clock.now()
        if answer == "alive":
            harness.execution_observation = _alive(now)
        elif answer == "exited":
            harness.execution_observation = ExecutionObservation(
                liveness=ExecutionLiveness.EXITED, activity=ExecutionActivity.UNKNOWN, evidence_at=now
            )
        else:
            harness.execution_observation = ExecutionObservation.unknown("cannot tell")
        ExecutionObserve(ctx).run()
        Reap(ctx).run()


# --- liveness ----------------------------------------------------------------------------------


def test_alive_evidence_each_minute_keeps_a_tool_running_past_the_hour_from_being_reaped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW)
    ctx, harness = _ctx(store, clock)

    _minutes(ctx, harness, clock, store, 90)

    assert _still_held(store)
    assert harness.observation_requests[-1].session == SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION)
    assert harness.observation_requests[-1].generation == 1


def test_once_evidence_stops_the_lease_is_reaped_an_hour_after_the_last_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW)
    ctx, harness = _ctx(store, clock)
    _minutes(ctx, harness, clock, store, 30)
    last_evidence = clock.now()

    _minutes(ctx, harness, clock, store, 60, answer="unknown")
    assert clock.now() - last_evidence == HEARTBEAT_STALENESS_THRESHOLD
    assert _still_held(store)

    _minutes(ctx, harness, clock, store, 1, answer="unknown")
    assert not _still_held(store)


def test_unknown_answers_never_renew_a_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW)
    ctx, harness = _ctx(store, clock)

    _minutes(ctx, harness, clock, store, 61, answer="unknown")

    assert not _still_held(store)


def test_exited_answers_never_renew_a_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW)
    ctx, harness = _ctx(store, clock)

    _minutes(ctx, harness, clock, store, 61, answer="exited")

    assert not _still_held(store)


def test_an_older_generations_evidence_never_renews_the_current_one(tmp_path: Path) -> None:
    """Evidence the previous spawn produced, even stamped after the respawn, renews nothing."""
    store = _store(tmp_path)
    _seed(store)
    respawned_at = _NOW + _MINUTE
    _spawn(store, at=respawned_at)
    late = respawned_at + timedelta(minutes=30)
    store.record_execution_observation(
        lease_id="lease_1", chunk_id="ch_1", generation=1, observation=_alive(late), observed_at=late
    )
    clock = FixedClock(respawned_at + HEARTBEAT_STALENESS_THRESHOLD + _MINUTE)
    ctx, _ = _ctx(store, clock)

    Reap(ctx).run()

    assert not _still_held(store)


def test_evidence_from_before_the_current_spawn_never_renews_it(tmp_path: Path) -> None:
    """An answer whose evidence predates the generation's own spawn is another execution's — not this one's."""
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW)
    ctx, harness = _ctx(store, clock)
    for _ in range(61):
        clock.advance(_MINUTE)
        harness.execution_observation = _alive(_NOW - _MINUTE)
        ExecutionObserve(ctx).run()

    Reap(ctx).run()

    assert not _still_held(store)


def test_an_exited_worker_with_fresh_evidence_is_still_left_to_advance(tmp_path: Path) -> None:
    """The pid check, not the harness's answer, drives exit-is-done: REAP leaves a dead worker alone."""
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW + HEARTBEAT_STALENESS_THRESHOLD + _MINUTE)
    ctx, harness = _ctx(store, clock, alive=False)
    harness.execution_observation = _alive(clock.now())

    ExecutionObserve(ctx).run()
    Reap(ctx).run()

    assert _still_held(store)


def test_an_unspawned_lease_is_not_observed_and_is_still_reaped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store, spawned=False)
    clock = FixedClock(_NOW + _MINUTE)
    ctx, harness = _ctx(store, clock)

    ExecutionObserve(ctx).run()
    Reap(ctx).run()

    assert harness.observation_requests == []
    assert not _still_held(store)


def test_a_parked_lease_is_never_observed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="q_1", parked_at=_NOW)
    clock = FixedClock(_NOW + NO_PROGRESS_THRESHOLD + _MINUTE)
    ctx, harness = _ctx(store, clock)

    ExecutionObserve(ctx).run()

    assert harness.observation_requests == []
    assert store.execution_observation_states(["lease_1"]) == {}


def test_a_taken_over_lease_is_never_observed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    record_takeover_in_unit(
        store,
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION),
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    clock = FixedClock(_NOW + NO_PROGRESS_THRESHOLD + _MINUTE)
    ctx, harness = _ctx(store, clock)

    ExecutionObserve(ctx).run()

    assert harness.observation_requests == []


def test_a_lease_is_observed_at_most_once_a_minute(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW + _MINUTE)
    ctx, harness = _ctx(store, clock)

    ExecutionObserve(ctx).run()
    clock.advance(timedelta(seconds=30))
    ExecutionObserve(ctx).run()
    clock.advance(timedelta(seconds=30))
    ExecutionObserve(ctx).run()

    assert len(harness.observation_requests) == 2


def test_an_adapter_that_raises_is_recorded_as_unknown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW + _MINUTE)
    ctx, _ = _ctx(store, clock, harness=_RaisingHarness(handle=_HANDLE, verdict="pass"))

    ExecutionObserve(ctx).run()

    state = store.execution_observation_states(["lease_1"])["lease_1"]
    assert state.last_observed_at == clock.now()
    assert store.liveness_facts(["lease_1"])["lease_1"].latest_evidence is None


# --- the no-progress warning --------------------------------------------------------------------


def test_fifteen_minutes_without_progress_warns_exactly_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW)
    ctx, harness = _ctx(store, clock)

    _minutes(ctx, harness, clock, store, 15)
    assert _warnings(store) == []

    _minutes(ctx, harness, clock, store, 1)
    warnings = _warnings(store)
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning["severity"] == "warning"
    assert warning["lease_id"] == "lease_1"
    assert warning["detail"]["activity"] == "tool"
    assert warning["detail"]["evidence_age_seconds"] == 0
    assert warning["detail"]["progress_age_seconds"] == 16 * 60
    assert warning["detail"]["harness_progress_known"] is False

    # Liveness-only observations neither repeat the warning nor clear it.
    _minutes(ctx, harness, clock, store, 30)
    assert len(_warnings(store)) == 1


def test_a_heartbeat_re_arms_the_warning(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW)
    ctx, harness = _ctx(store, clock)
    _minutes(ctx, harness, clock, store, 20)
    assert len(_warnings(store)) == 1

    store.record_heartbeat(lease_id="lease_1", beat_at=clock.now())
    _minutes(ctx, harness, clock, store, 15)
    assert len(_warnings(store)) == 1
    _minutes(ctx, harness, clock, store, 1)

    assert len(_warnings(store)) == 2


def test_harness_reported_progress_moves_the_anchor_and_re_arms(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW)
    ctx, harness = _ctx(store, clock)
    _minutes(ctx, harness, clock, store, 16)
    assert len(_warnings(store)) == 1

    clock.advance(_MINUTE)
    harness.execution_observation = _alive(clock.now(), progress=clock.now())
    ExecutionObserve(ctx).run()
    _minutes(ctx, harness, clock, store, 16)

    warnings = _warnings(store)
    assert len(warnings) == 2
    # The anchor came from an earlier observation; this one reports none of its own.
    assert warnings[1]["detail"]["progress_age_seconds"] == 16 * 60


def test_an_unknown_answer_never_warns(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW)
    ctx, harness = _ctx(store, clock)

    _minutes(ctx, harness, clock, store, 30, answer="unknown")

    assert _warnings(store) == []


def test_the_warning_is_announced_as_its_buffered_fact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    clock = FixedClock(_NOW + NO_PROGRESS_THRESHOLD + _MINUTE)
    ctx, harness = _ctx(store, clock)
    harness.execution_observation = _alive(clock.now())

    ExecutionObserve(ctx).run()

    states = store.execution_observation_states(["lease_1"])
    warned = states["lease_1"].warned
    assert warned is not None
    assert warned.generation == 1
    assert warned.anchor_at == _NOW
