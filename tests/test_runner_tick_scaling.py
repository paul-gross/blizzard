"""A tick's per-lease handlers re-read nothing the tick already holds — loop component tier.

Each arm seeds N leases in one state, runs a warm-up tick, then counts the statements of the
next. Every arm must stay linear in N; the exited-awaiting and pause-parked arms, whose
per-lease work the tick hoists, must stay flat. The adopt arm is one-shot, so its first tick
on each fresh store is the measure."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import RUNNER_LOCALLY_PAUSED
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.hub.client import HubQuestion
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.tick import tick
from blizzard.runner.node_steps.chunk_state import ChunkState
from tests import support
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    runner_store_errors,
    serving,
)
from tests.test_runner_tick_bulk_reads import migrated_engine

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)
_SIZES = (1, 2, 4)


class _Run:
    """One fresh store at one N: its engine, hub and the context a tick runs against."""

    def __init__(self, tick_once: Callable[[], None], engine: Engine, hub: FakeHub) -> None:
        self.tick_once = tick_once
        self.engine = engine
        self.hub = hub

    def measure(self, *, warm_up: bool) -> int:
        if warm_up:
            self.tick_once()
        return support.count_queries(self.engine, self.tick_once)


def _seed_lease(store: SqlAlchemyRunnerStore, i: int) -> None:
    """One active, spawned, beating, bound lease whose worker pid is never in the probe: dead."""
    lease_id, chunk_id = f"lease_{i}", f"ch_{i}"
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease_id,
        pid=1000 + i,
        process_start_time=f"t{i}",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{i}"),
        spawned_at=_NOW,
    )
    store.record_heartbeat(lease_id=lease_id, beat_at=_NOW)
    store.record_binding(chunk_id=chunk_id, environment_id=f"e{i}", workdir=f"/ws/e{i}", bound_at=_NOW)


def _run(tmp_path: Path, n: int, seed: Callable[[SqlAlchemyRunnerStore, FakeHub, int], None]) -> _Run:
    engine = migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    hub = FakeHub()
    for i in range(n):
        hub.chunks[f"ch_{i}"] = ChunkState(
            chunk_id=f"ch_{i}", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1"
        )
        hub.envelopes[f"ch_{i}"] = make_envelope(
            f"ch_{i}", "build", node_id="nd_build", choices=[("pass", "ok"), ("fail", "no")]
        )
        seed(store, hub, i)
    handle = WorkerHandle(session_id="unused", pid=1, process_start_time="unused", pgid=1)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({f"e{i}": f"/ws/e{i}" for i in range(n)}),
        harness=FakeHarness(handle=handle, verdict="pass"),
        probe=FakeProbe(alive={(2000 + i, f"p{i}") for i in range(n)}),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_name="r1", runner_id=FIXTURE_RUNNER_ID, bindings=serving(n), agents=n),
    )
    return _Run(lambda: tick(ctx), engine, hub)


def _counts(
    tmp_path: Path, seed: Callable[[SqlAlchemyRunnerStore, FakeHub, int], None], *, warm_up: bool = True
) -> list[int]:
    return [_run(tmp_path / f"n{n}", n, seed).measure(warm_up=warm_up) for n in _SIZES]


def _assert_linear(counts: list[int]) -> None:
    one, two, four = counts
    assert four - two <= 2 * (two - one), counts


def _assert_flat(counts: list[int]) -> None:
    assert len(set(counts)) == 1, counts


# --- exited, awaiting its verdict elicitation ------------------------------------------------------


def _seed_exited_awaiting(store: SqlAlchemyRunnerStore, hub: FakeHub, i: int) -> None:
    _seed_lease(store, i)  # the worker's pid 1000+i is never in the probe: dead
    store.record_elicitation_launch(f"lease_{i}", 1, output_path=f"/tmp/elicit-{i}", at=_NOW)
    store.record_elicitation_started(f"lease_{i}", 1, pid=2000 + i, process_start_time=f"p{i}", pgid=2000 + i)


def test_the_exited_awaiting_arm_is_linear_and_flat_in_leases(tmp_path: Path) -> None:
    counts = _counts(tmp_path, _seed_exited_awaiting)

    _assert_linear(counts)
    _assert_flat(counts)


# --- pause-parked --------------------------------------------------------------------------


def _seed_pause_parked(store: SqlAlchemyRunnerStore, hub: FakeHub, i: int) -> None:
    _seed_lease(store, i)
    # Only the local brake stops a start; a hub pause alone lets an unpaused park resume.
    store.record_local_pause(
        paused=True,
        at=_NOW,
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED,
        report_payload=json.dumps({"by": "operator"}),
    )
    store.record_pause_park(lease_id=f"lease_{i}", chunk_id=f"ch_{i}", parked_at=_NOW)


def test_the_pause_parked_arm_is_linear_and_flat_in_leases(tmp_path: Path) -> None:
    counts = _counts(tmp_path, _seed_pause_parked)

    _assert_linear(counts)
    _assert_flat(counts)


# --- ask-parked -----------------------------------------------------------------------------


def _seed_ask_parked(store: SqlAlchemyRunnerStore, hub: FakeHub, i: int) -> None:
    _seed_lease(store, i)
    question_id = f"qn_{i}"
    store.record_ask(
        lease_id=f"lease_{i}",
        chunk_id=f"ch_{i}",
        question_id=question_id,
        question="Which API?",
        options=["rest", "graphql"],
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{i}"),
        asked_at=_NOW,
    )
    store.record_park(lease_id=f"lease_{i}", chunk_id=f"ch_{i}", question_id=question_id, parked_at=_NOW)
    hub.questions[question_id] = HubQuestion(
        question_id=question_id,
        chunk_id=f"ch_{i}",
        runner_id="r1",
        epoch=1,
        question="Which API?",
        asked_at=(_NOW - timedelta(minutes=1)).isoformat(),
    )


def test_the_ask_parked_arm_is_linear_in_statements_and_in_question_reads(tmp_path: Path) -> None:
    runs = [_run(tmp_path / f"n{n}", n, _seed_ask_parked) for n in _SIZES]
    statements = [run.measure(warm_up=True) for run in runs]
    questions = [len(run.hub.get_question_calls) for run in runs]

    _assert_linear(statements)
    _assert_linear(questions)


# --- adopt-candidate ---------------------------------------------------------------------------


def _seed_adopt_candidate(store: SqlAlchemyRunnerStore, hub: FakeHub, i: int) -> None:
    store.record_binding(chunk_id=f"ch_{i}", environment_id=f"e{i}", workdir=f"/ws/e{i}", bound_at=_NOW)


def test_the_adopt_arm_is_linear_in_held_chunks(tmp_path: Path) -> None:
    _assert_linear(_counts(tmp_path, _seed_adopt_candidate, warm_up=False))
