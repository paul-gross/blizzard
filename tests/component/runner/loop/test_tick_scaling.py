"""A tick's per-lease handlers re-read nothing the tick already holds — loop component tier.

Each arm seeds N leases or held chunks and counts one tick's statements, after a warm-up tick except
in the one-shot adopt arm. It holds that count flat or to its measured per-item slope; a change
adding a legitimate per-item read raises the arm's budget."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import RUNNER_LOCALLY_PAUSED
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.hub.client import HubQuestion
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle import claim
from blizzard.runner.lifecycle.ownership_reconcile import OwnershipReconcile
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.tick import tick
from blizzard.runner.node_steps.chunk_state import ChunkState
from tests.support import scaffolding as support
from tests.support.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    runner_store_errors,
    serving,
    usage_limit_mark,
)
from tests.support.runner_mint import new_mint
from tests.support.runner_tick_engines import migrated_engine

_NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)
_SIZES = (1, 2, 4)


class _Run:
    """One fresh store at one N: its engine, hub and the context a tick runs against."""

    def __init__(self, tick_once: Callable[[], None], engine: Engine, hub: FakeHub, ctx: Any = None) -> None:
        self.ctx = ctx
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
    store.record_mint(
        new_mint(
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


def _run(
    tmp_path: Path,
    n: int,
    seed: Callable[[SqlAlchemyRunnerStore, FakeHub, int], None],
    *,
    ship: bool = False,
    alive: bool = False,
) -> _Run:
    """``ship`` turns transcript shipping on; ``alive`` leaves each seeded worker running."""
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
    running: set[tuple[int, str | None]] = {(2000 + i, f"p{i}") for i in range(n)}  # the elicitations
    if alive:
        running |= {(1000 + i, f"t{i}") for i in range(n)}  # the workers
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({f"e{i}": f"/ws/e{i}" for i in range(n)}),
        harness=FakeHarness(handle=handle, verdict="pass"),
        probe=FakeProbe(alive=running),
        clock=FixedClock(_NOW),
        config=LoopConfig(
            runner_name="r1", runner_id=FIXTURE_RUNNER_ID, bindings=serving(), agents=n, transcripts_ship=ship
        ),
    )
    return _Run(lambda: tick(ctx), engine, hub, ctx)


def _counts(
    tmp_path: Path, seed: Callable[[SqlAlchemyRunnerStore, FakeHub, int], None], *, warm_up: bool = True
) -> list[int]:
    return [_run(tmp_path / f"n{n}", n, seed).measure(warm_up=warm_up) for n in _SIZES]


def _assert_slope(counts: list[int], per_item: int) -> None:
    """At most ``per_item`` more of whatever ``counts`` counts for each added lease or chunk."""
    _, two, four = counts
    assert four - two <= 2 * per_item, counts


def _assert_flat(counts: list[int]) -> None:
    assert len(set(counts)) == 1, counts


# --- exited, awaiting its verdict elicitation ------------------------------------------------------


def _seed_exited_awaiting(store: SqlAlchemyRunnerStore, hub: FakeHub, i: int) -> None:
    _seed_lease(store, i)  # the worker's pid 1000+i is never in the probe: dead
    store.record_elicitation_launch(f"lease_{i}", 1, output_path=f"/tmp/elicit-{i}", at=_NOW)
    store.record_elicitation_started(f"lease_{i}", 1, pid=2000 + i, process_start_time=f"p{i}", pgid=2000 + i)


def test_the_exited_awaiting_arm_is_flat_in_leases(tmp_path: Path) -> None:
    _assert_flat(_counts(tmp_path, _seed_exited_awaiting))


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


def test_the_pause_parked_arm_is_flat_in_leases(tmp_path: Path) -> None:
    _assert_flat(_counts(tmp_path, _seed_pause_parked))


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


def test_the_ask_parked_arm_holds_its_statement_and_question_read_slopes(tmp_path: Path) -> None:
    runs = [_run(tmp_path / f"n{n}", n, _seed_ask_parked) for n in _SIZES]
    statements: list[int] = []
    questions: list[int] = []
    for run in runs:
        run.tick_once()
        asked = len(run.hub.get_question_calls)
        statements.append(run.measure(warm_up=False))
        questions.append(len(run.hub.get_question_calls) - asked)

    # The throttle floor's newest mark and newest clear, read for each lease's wake; the pass's brakes and the
    # open parks each arrive from one read.
    _assert_slope(statements, 2)
    _assert_slope(questions, 1)  # each lease polls its own question


# --- adopt-candidate ---------------------------------------------------------------------------


def _seed_adopt_candidate(store: SqlAlchemyRunnerStore, hub: FakeHub, i: int) -> None:
    store.record_binding(chunk_id=f"ch_{i}", environment_id=f"e{i}", workdir=f"/ws/e{i}", bound_at=_NOW)


def test_the_adopt_arm_holds_its_slope_in_held_chunks(tmp_path: Path) -> None:
    # Per adopted chunk: the adopt and its spawn, then the same tick's ADVANCE and execution observation of the
    # new lease, whose pid the probe does not hold, so ADVANCE takes it as exited and launches its elicitation.
    _assert_slope(_counts(tmp_path, _seed_adopt_candidate, warm_up=False), 62)


# --- held in adopt: the node entry is not FILL's ---------------------------------------------


def _seed_adopt_held(store: SqlAlchemyRunnerStore, hub: FakeHub, i: int) -> None:
    # Escalated at the epoch the hub still reports running, so every tick weighs the adopt and declines it.
    _seed_lease(store, i)
    store.record_closure(
        lease_id=f"lease_{i}", chunk_id=f"ch_{i}", node_id="nd_build", reason="escalated", closed_at=_NOW
    )


def test_the_held_adopt_arm_holds_its_slope_in_held_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    verdicts: list[bool] = []
    real = claim.owns_node_entry

    def recording(*args: Any, **kwargs: Any) -> bool:
        verdicts.append(real(*args, **kwargs))
        return verdicts[-1]

    monkeypatch.setattr(claim, "owns_node_entry", recording)
    runs = [_run(tmp_path / f"n{n}", n, _seed_adopt_held) for n in _SIZES]
    statements = [run.measure(warm_up=True) for run in runs]

    assert verdicts == [False] * 2 * sum(_SIZES), verdicts  # warm-up and measured tick, every held chunk
    # The held-chunk drive's own latest epoch, a lease max and a takeover-fence max; the adopt's facts are batched.
    _assert_slope(statements, 2)


# --- the ownership reconcile ----------------------------------------------------------------


def test_the_ownership_reconcile_reads_open_takeovers_once_per_pass(tmp_path: Path) -> None:
    run = _run(tmp_path, 2, _seed_ask_parked)
    run.ctx.identity.hold(RunnerIdentity(FIXTURE_RUNNER_ID, "r1", _NOW))  # registered: every phase runs

    with support.capture_statements(run.engine) as statements:
        OwnershipReconcile(run.ctx).run()

    takeover_reads = [text for text, _ in statements if "FROM takeovers " in text]
    assert len(takeover_reads) == 1, takeover_reads


# --- overload backoff ------------------------------------------------------------------------


def _seeder_backing_off(resume_after: datetime) -> Callable[[SqlAlchemyRunnerStore, FakeHub, int], None]:
    def seed(store: SqlAlchemyRunnerStore, hub: FakeHub, i: int) -> None:
        _seed_lease(store, i)
        store.record_overload(
            lease_id=f"lease_{i}",
            chunk_id=f"ch_{i}",
            epoch=1,
            generation=1,
            invocation_kind="worker",
            invocation_identity="1",
            streak_ordinal=1,
            observed_at=_NOW - timedelta(minutes=5),
            resume_after=resume_after,
        )

    return seed


def _brake_reads(tmp_path: Path, seed: Callable[[SqlAlchemyRunnerStore, FakeHub, int], None], n: int) -> int:
    run = _run(tmp_path, n, seed)
    with support.capture_statements(run.engine) as statements:
        run.tick_once()
    return sum(1 for text, _ in statements if "FROM local_pause_facts" in text or "FROM hub_control" in text)


def test_the_backoff_wakes_read_the_brakes_per_pass_not_per_lease(tmp_path: Path) -> None:
    due = _seeder_backing_off(_NOW - timedelta(minutes=1))
    not_due = _seeder_backing_off(_NOW + timedelta(hours=1))

    due_reads = [_brake_reads(tmp_path / f"due{n}", due, n) for n in _SIZES]
    not_due_reads = [_brake_reads(tmp_path / f"idle{n}", not_due, n) for n in _SIZES]

    _assert_flat(due_reads)
    _assert_flat(not_due_reads)
    # A not-due pass reads no brakes for the wake: it reads fewer than a due pass does.
    assert not_due_reads[0] < due_reads[0], (not_due_reads, due_reads)


def _seed_backing_off_under_a_declining_floor(store: SqlAlchemyRunnerStore, hub: FakeHub, i: int) -> None:
    # The floor declines the wake, so the lease stays due and is weighed again every tick.
    _seeder_backing_off(_NOW - timedelta(minutes=1))(store, hub, i)
    store.record_usage_limit_mark(usage_limit_mark(_NOW, resets_in=timedelta(hours=1)))


def test_the_due_backoff_arm_holds_its_slope_in_leases(tmp_path: Path) -> None:
    runs = [_run(tmp_path / f"n{n}", n, _seed_backing_off_under_a_declining_floor) for n in _SIZES]
    statements: list[int] = []
    floor_reads: list[int] = []
    for run in runs:
        run.tick_once()
        with support.capture_statements(run.engine) as captured:
            run.tick_once()
        statements.append(len(captured))
        floor_reads.append(sum(1 for text, _ in captured if "FROM runner_floor_clears" in text))

    assert floor_reads[2] - floor_reads[1] == 2, floor_reads  # every lease is still due and asks the floor
    # The throttle floor's newest mark and newest clear per lease; the brakes are read once a pass.
    _assert_slope(statements, 2)


# --- the transcript pump ---------------------------------------------------------------------


def _seed_live_lease(store: SqlAlchemyRunnerStore, hub: FakeHub, i: int) -> None:
    _seed_lease(store, i)


def test_the_transcript_pump_arm_is_flat_in_open_segments(tmp_path: Path) -> None:
    """Live workers, shipping on: each spawn opened a segment, and the pump reads every one of them. The fake
    source answers unavailable, so the count is the pump's reads ahead of the source and not its writes."""
    runs = [_run(tmp_path / f"n{n}", n, _seed_live_lease, ship=True, alive=True) for n in _SIZES]
    statements = [run.measure(warm_up=True) for run in runs]

    for n, run in zip(_SIZES, runs, strict=True):
        source = run.ctx.harnesses.transcript_source(CLAUDE_CODE_HARNESS_ID)
        assert isinstance(source, FakeTranscriptSource)
        assert len(source.turns_since_calls) == 2 * n  # both ticks reached every segment
    _assert_flat(statements)
