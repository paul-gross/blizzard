"""The runner tick's bulk-read seams — REAP, FILL, ADVANCE, ContextSample, and
``backing_off_facts``, each collapsed from a per-lease/per-fact read to a plural keyed by
the id set the caller already holds (`bzh:bulk-reconstitution`).

Two shapes per new plural: it matches its singular sibling and drops an unknown id, and its
own statement count is flat across a lowered ``BATCH_SIZE`` boundary. The tick tests drive a
full ``tick(ctx)`` at N=1 and N=10 steady-state leases and pin the statement count equal —
the test the work item calls out as mattering more than any single fix. Later sections do the
same for ``RunnerStatusService.escalations()`` (runner-API read path), the interrupted-claim
sweep's takeover and escalation reads, and the active-lease chunk-id projection."""

from __future__ import annotations

from concurrent.futures import Executor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

import pytest
from sqlalchemy import Engine

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store import batching as batching_module
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.adapter import ClaudeCodeAdapter
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.observation import ExecutionActivity, ExecutionLiveness, ExecutionObservation
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.leases.model import NewLease
from blizzard.runner.leases.overload import backing_off_facts
from blizzard.runner.lifecycle.claim import InterruptedClaims
from blizzard.runner.lifecycle.resume_intents import ResumeIntents
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.tick import tick
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.internal.worker_scratch_dirs import WorkerScratchDirs
from blizzard.runner.process.scratch_cleanup import sweep_closed_legacy
from blizzard.runner.status.view import RunnerStatusService
from tests.support import scaffolding as support
from tests.support.child_launch import CHILDREN, version_probe
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    make_stores,
    registered_identity,
    runner_store_errors,
    serving,
    single_workspace,
)
from tests.support.runner_mint import new_mint
from tests.support.runner_tick_engines import migrated_engine
from tests.support.takeover_unit import record_takeover_in_unit

_NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> tuple[SqlAlchemyRunnerStore, Engine]:
    engine = migrated_engine(tmp_path)
    return SqlAlchemyRunnerStore(engine, runner_store_errors()), engine


# --- liveness_facts ----------------------------------------------------------------------


def test_liveness_facts_matches_the_singulars_and_drops_an_unknown_id(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    store.record_heartbeat(lease_id="lease_2", beat_at=_NOW)

    result = store.liveness_facts(["lease_1", "lease_2", "lease_never_beat"])

    assert set(result) == {"lease_1", "lease_2"}
    for lease_id in ("lease_1", "lease_2"):
        assert result[lease_id].latest_heartbeat == store.latest_heartbeat(lease_id)
        assert result[lease_id].latest_spawn == store.latest_spawn(lease_id)


def test_liveness_facts_matches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    ids = [f"lease_{i}" for i in range(7)]
    for lease_id in ids:
        store.record_heartbeat(lease_id=lease_id, beat_at=_NOW)

    result = store.liveness_facts(ids)

    assert set(result) == set(ids)
    for lease_id in ids:
        assert result[lease_id].latest_heartbeat == store.latest_heartbeat(lease_id)


def test_liveness_facts_statement_count_is_flat_across_id_set_sizes(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    ids = [f"lease_{i}" for i in range(10)]
    for lease_id in ids:
        store.record_heartbeat(lease_id=lease_id, beat_at=_NOW)

    one = support.count_queries(engine, lambda: store.liveness_facts(ids[:1]))
    ten = support.count_queries(engine, lambda: store.liveness_facts(ids))

    assert one == ten


# --- lease_generations ---------------------------------------------------------------------


def _mint(store: SqlAlchemyRunnerStore, lease_id: str, chunk_id: str) -> None:
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


def test_legacy_cleanup_batches_candidate_closures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, engine = _store(tmp_path)
    ids = [f"lease_{i}" for i in range(7)]
    for i, lease_id in enumerate(ids):
        _mint(store, lease_id, f"ch_{i}")
        if i != 6:
            store.record_closure(
                lease_id=lease_id, chunk_id=f"ch_{i}", node_id="nd_build", reason="done", closed_at=_NOW
            )
    legacy = tmp_path / "legacy"
    scratch = WorkerScratchDirs(str(tmp_path / "scratch"), "rn_test", legacy_root=str(legacy))

    def run(names: list[str]) -> int:
        for name in names:
            (legacy / name).mkdir(parents=True, exist_ok=True)
        return support.count_queries(engine, lambda: sweep_closed_legacy(scratch, store, strict=True))

    assert run(ids[:1]) == run([*ids, "unknown"]) == 1
    assert (legacy / ids[-1]).is_dir()
    assert (legacy / "unknown").is_dir()
    assert all(not (legacy / name).exists() for name in ids[:-1])
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    assert run(ids) == 3


def test_lease_generations_matches_the_singular_and_drops_an_unknown_id(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    session = SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1")
    _mint(store, "lease_1", "ch_1")
    _mint(store, "lease_2", "ch_2")
    store.record_spawn("lease_1", pid=1, process_start_time="t1", session=session, spawned_at=_NOW)
    store.record_spawn("lease_1", pid=2, process_start_time="t2", session=session, spawned_at=_NOW)  # 2nd generation
    store.record_spawn("lease_2", pid=3, process_start_time="t3", session=session, spawned_at=_NOW)

    result = store.lease_generations(["lease_1", "lease_2", "lease_never_spawned"])

    assert result == {"lease_1": 2, "lease_2": 1}
    assert result["lease_1"] == store.lease_generation("lease_1")


def test_lease_generations_matches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    session = SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1")
    ids = [f"lease_{i}" for i in range(7)]
    for lease_id in ids:
        _mint(store, lease_id, f"ch_{lease_id}")
        store.record_spawn(lease_id, pid=1, process_start_time="t", session=session, spawned_at=_NOW)

    result = store.lease_generations(ids)

    assert result == dict.fromkeys(ids, 1)


# --- context_sample_states -----------------------------------------------------------------


def test_context_sample_states_matches_the_singular_and_drops_an_unknown_id(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    session = SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1")
    store.record_context_sample(
        lease_id="lease_1", chunk_id="ch_1", context_tokens=100, sampled_at=_NOW, session=session
    )
    store.record_context_sample(
        lease_id="lease_2", chunk_id="ch_2", context_tokens=200, sampled_at=_NOW, session=session
    )

    result = store.context_sample_states(["lease_1", "lease_2", "lease_never_sampled"])

    assert set(result) == {"lease_1", "lease_2"}
    assert result["lease_1"] == store.context_sample_state("lease_1")
    assert result["lease_2"] == store.context_sample_state("lease_2")


def test_context_sample_states_matches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    session = SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1")
    ids = [f"lease_{i}" for i in range(7)]
    for lease_id in ids:
        store.record_context_sample(
            lease_id=lease_id, chunk_id="ch", context_tokens=1, sampled_at=_NOW, session=session
        )

    result = store.context_sample_states(ids)

    assert set(result) == set(ids)
    for lease_id in ids:
        assert result[lease_id] == store.context_sample_state(lease_id)


# --- execution_observation_states and evidence ------------------------------------------------


def _alive(at: datetime) -> ExecutionObservation:
    return ExecutionObservation(
        liveness=ExecutionLiveness.ALIVE, activity=ExecutionActivity.TOOL, evidence_at=at, progress_at=at
    )


def test_execution_observation_states_matches_across_a_batch_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    ids = [f"lease_{i}" for i in range(7)]
    for i, lease_id in enumerate(ids):
        store.record_execution_observation(
            lease_id=lease_id,
            chunk_id="ch",
            generation=1,
            observation=_alive(_NOW),
            observed_at=_NOW,
            warned_anchor_at=_NOW if i % 2 else None,
        )
        store.record_execution_observation(
            lease_id=lease_id,
            chunk_id="ch",
            generation=1,
            observation=ExecutionObservation.unknown("later"),
            observed_at=_NOW + timedelta(minutes=1),
        )

    result = store.execution_observation_states([*ids, "lease_never_observed"])

    assert set(result) == set(ids)
    for i, lease_id in enumerate(ids):
        state = result[lease_id]
        assert state.last_observed_at == _NOW + timedelta(minutes=1)
        assert state.latest_progress_at == _NOW
        assert (state.warned is not None) == bool(i % 2)


def test_execution_observation_states_statement_count_is_flat_across_id_set_sizes(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    ids = [f"lease_{i}" for i in range(10)]
    for lease_id in ids:
        store.record_execution_observation(
            lease_id=lease_id, chunk_id="ch", generation=1, observation=_alive(_NOW), observed_at=_NOW
        )

    one = support.count_queries(engine, lambda: store.execution_observation_states(ids[:1]))
    ten = support.count_queries(engine, lambda: store.execution_observation_states(ids))

    assert one == ten


def test_liveness_facts_carries_the_newest_generations_evidence_never_one_produced_after_its_sample(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    for generation, at in ((1, _NOW), (2, _NOW + timedelta(minutes=5))):
        store.record_execution_observation(
            lease_id="lease_1", chunk_id="ch", generation=generation, observation=_alive(at), observed_at=at
        )
    # Evidence stamped after its own sample is no evidence.
    store.record_execution_observation(
        lease_id="lease_1",
        chunk_id="ch",
        generation=2,
        observation=_alive(_NOW + timedelta(hours=1)),
        observed_at=_NOW + timedelta(minutes=6),
    )
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)

    facts = store.liveness_facts(["lease_1"])["lease_1"]

    assert facts.latest_evidence is not None
    assert facts.latest_evidence.generation == 2
    assert facts.latest_evidence.evidence_at == _NOW + timedelta(minutes=5)


# --- in_flight_elicitations ------------------------------------------------------------------


def test_in_flight_elicitations_matches_the_singular_and_drops_an_unknown_pair(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    store.record_elicitation_launch("lease_1", 1, output_path="/tmp/a", at=_NOW)
    store.record_elicitation_launch("lease_2", 1, output_path="/tmp/b", at=_NOW)

    result = store.in_flight_elicitations([("lease_1", 1), ("lease_2", 1), ("lease_1", 2), ("lease_never", 1)])

    assert set(result) == {("lease_1", 1), ("lease_2", 1)}
    assert result[("lease_1", 1)] == store.in_flight_elicitation("lease_1", 1)


def test_in_flight_elicitations_matches_across_a_batch_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    pairs = [(f"lease_{i}", 1) for i in range(7)]
    for lease_id, epoch in pairs:
        store.record_elicitation_launch(lease_id, epoch, output_path="/tmp/p", at=_NOW)

    result = store.in_flight_elicitations(pairs)

    assert set(result) == set(pairs)
    for lease_id, epoch in pairs:
        assert result[(lease_id, epoch)] == store.in_flight_elicitation(lease_id, epoch)


# --- backing_off_facts ------------------------------------------------------------------------


def _seed_open_worker_overload(store: SqlAlchemyRunnerStore, lease_id: str) -> None:
    """A fact whose generation matches (never spawned — generation 0, identity "0") so it
    reads as still backing off (`backing_off_facts`'s own implicit-close rule)."""
    store.record_overload(
        lease_id=lease_id,
        chunk_id=f"ch-{lease_id}",
        epoch=1,
        generation=1,
        invocation_kind="worker",
        invocation_identity="0",
        streak_ordinal=1,
        observed_at=_NOW,
        resume_after=_NOW,
    )


def test_backing_off_facts_statement_count_is_flat_across_1_and_10_open_facts(tmp_path: Path) -> None:
    store, engine = _store(tmp_path / "one")
    _seed_open_worker_overload(store, "lease_0")
    one = support.count_queries(engine, lambda: backing_off_facts(store, store, store))

    store2, engine2 = _store(tmp_path / "ten")
    for i in range(10):
        _seed_open_worker_overload(store2, f"lease_{i}")
    ten = support.count_queries(engine2, lambda: backing_off_facts(store2, store2, store2))

    assert one == ten
    assert len(backing_off_facts(store2, store2, store2)) == 10


# --- the tick itself ----------------------------------------------------------------------


#: A binding as FILL writes it — named by workspace and project, so no tick completes it.
class _Complete(TypedDict):
    workspace_id: str
    project_id: str


_COMPLETE: _Complete = {"workspace_id": "ws1", "project_id": "pr_1"}


def _seed_steady_state_lease(store: SqlAlchemyRunnerStore, i: int, *, at: datetime, sampled_at: datetime) -> None:
    """One active, spawned, beating, bound lease, sampled at ``sampled_at`` — sampled at ``at``
    nothing about it is due for any per-tick action, so a flat tick issues the same
    statements regardless of i."""
    lease_id, chunk_id, session_id = f"lease_{i}", f"ch_{i}", f"sess-{i}"
    session = SessionReference(CLAUDE_CODE_HARNESS_ID, session_id)
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
                created_at=at,
            )
        )
    )
    store.record_spawn(lease_id, pid=1000 + i, process_start_time=f"t{i}", session=session, spawned_at=at)
    store.record_heartbeat(lease_id=lease_id, beat_at=at)
    store.record_binding(chunk_id=chunk_id, environment_id=f"e{i}", workdir=f"/ws/e{i}", bound_at=at, **_COMPLETE)
    store.record_context_sample(
        lease_id=lease_id, chunk_id=chunk_id, context_tokens=100, sampled_at=sampled_at, session=session
    )
    store.record_execution_observation(
        lease_id=lease_id,
        chunk_id=chunk_id,
        generation=1,
        observation=ExecutionObservation.unknown("seeded"),
        observed_at=sampled_at,
    )


def _tick_statement_count(
    tmp_path: Path, n: int, *, ship: bool = False, due: bool = False, held_without_lease: int = 0
) -> int:
    """One full tick's statement count over ``n`` leases. ``due`` backdates every lease's last
    sample past the interval; ``held_without_lease`` adds live-tenure bindings no lease holds."""
    store, engine = _store(tmp_path)
    for i in range(n):
        sampled_at = _NOW - timedelta(hours=1) if due else _NOW
        _seed_steady_state_lease(store, i, at=_NOW, sampled_at=sampled_at)
    for j in range(held_without_lease):
        store.record_binding(
            chunk_id=f"ch_held_{j}", environment_id=f"eh{j}", workdir=f"/ws/eh{j}", bound_at=_NOW, **_COMPLETE
        )
    # Every session is unscripted (`FakeTranscriptSource.turns_since` reads that as
    # `available=False`), so turning shipping on still ships nothing this tick — the pump
    # runs its own bulk reads but writes nothing, keeping the count flat either way.
    source = FakeTranscriptSource(context_tokens_by_session={f"sess-{i}": 100 for i in range(n)})
    # No spawn happens this tick (every lease is already spawned) — the handle is never read.
    handle = WorkerHandle(session_id="unused", pid=1, process_start_time="unused", pgid=1)
    harness = FakeHarness(handle=handle, verdict="pass", transcript_source=source)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({f"e{i}": f"/ws/e{i}" for i in range(n)}),
        harness=harness,
        probe=FakeProbe(alive={(1000 + i, f"t{i}") for i in range(n)}),
        clock=FixedClock(_NOW),
        config=LoopConfig(
            runner_name="r1",
            runner_id="rn_1",
            bindings=serving(),
            agents=n,
            context_warn_tokens=300_000,
            context_sample_interval_seconds=60,
            transcripts_ship=ship,
        ),
    )
    return support.count_queries(engine, lambda: tick(ctx))


def test_a_full_tick_issues_the_same_statement_count_at_n1_and_n10_alive_leases(tmp_path: Path) -> None:
    """The work item's own test: at steady state — nothing stale, nothing due, nothing to
    claim — a tick's statement count must not grow with the number of active leases."""
    one = _tick_statement_count(tmp_path / "n1", 1)
    ten = _tick_statement_count(tmp_path / "n10", 10)

    assert one == ten


def test_a_full_tick_is_still_flat_at_n1_and_n10_with_transcript_shipping_on(tmp_path: Path) -> None:
    """With shipping on, one open segment per lease (every spawn opens one), the pump's own
    bulk reads must not turn the tick's flat statement count back into a per-lease slope."""
    one = _tick_statement_count(tmp_path / "n1", 1, ship=True)
    ten = _tick_statement_count(tmp_path / "n10", 10, ship=True)

    assert one == ten


def test_a_full_tick_adds_two_statements_per_lease_when_every_lease_is_due_for_a_sample(tmp_path: Path) -> None:
    """The context-sample INSERT and the execution-observation INSERT are legitimately per lease;
    the binding read that locates the transcript, and every fact either sampler judges by, must
    not add a third."""
    one = _tick_statement_count(tmp_path / "n1", 1, due=True)
    ten = _tick_statement_count(tmp_path / "n10", 10, due=True)

    assert ten - one == 18


def test_a_full_tick_is_flat_across_1_and_10_held_chunks_with_no_lease(tmp_path: Path) -> None:
    """A chunk parked at a hub node keeps its binding with no lease; reconciling it must not
    read bindings once per held chunk."""
    one = _tick_statement_count(tmp_path / "h1", 1, held_without_lease=1)
    ten = _tick_statement_count(tmp_path / "h10", 1, held_without_lease=10)

    assert one == ten


# --- RunnerStatusService.escalations() ---------------------------------------------


def _status_service(spawn_executor: Executor, store: SqlAlchemyRunnerStore) -> RunnerStatusService:
    probe = FakeProbe()
    return RunnerStatusService(
        FixedClock(_NOW),
        pause=store,
        daemon_liveness=store,
        lease_record=store,
        outbound=store,
        environments=store,
        asks=store,
        takeover=store,
        escalations=store,
        identity=registered_identity("r1"),
        runner_name="r1",
        home_workspace=lambda: "ws1",
        bindings=serving(),
        agents=10,
        hub_url="http://hub",
        workspaces=single_workspace(FakeProvider({}), spawn_root="/ws"),
        harnesses=HarnessRegistry(
            {
                CLAUDE_CODE_HARNESS_ID: HarnessBinding(
                    adapter=ClaudeCodeAdapter(
                        worker_env=AllowlistedEnv.of(()),
                        binary="claude",
                        permission_mode="bypassPermissions",
                        process=probe,
                        launcher=ProcessLauncher(probe, executor=spawn_executor, children=CHILDREN),
                        versions=version_probe(),
                    )
                )
            }
        ),
    )


def _seed_escalated_lease(store: SqlAlchemyRunnerStore, i: int, *, at: datetime) -> None:
    lease_id, chunk_id, session_id = f"lease_{i}", f"ch_{i}", f"sess-{i}"
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
                created_at=at,
            )
        )
    )
    store.record_spawn(
        lease_id,
        pid=1000 + i,
        process_start_time=f"t{i}",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session_id),
        spawned_at=at,
    )
    store.record_binding(chunk_id=chunk_id, environment_id=f"e{i}", workdir=f"/ws/e{i}", bound_at=at, **_COMPLETE)
    store.record_closure(
        lease_id=lease_id,
        chunk_id=chunk_id,
        node_id="nd_build",
        reason="escalated",
        closed_at=at + timedelta(minutes=1),
    )


def test_escalations_statement_count_is_flat_across_1_and_10_open_escalations(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    """Resolving every parked escalation's resume command must read the fleet's held
    bindings once, not once per escalation (`bindings_for_chunk` per escalation would slope
    with the open-escalation count)."""
    store, engine = _store(tmp_path / "one")
    _seed_escalated_lease(store, 0, at=_NOW)
    one = support.count_queries(engine, lambda: _status_service(spawn_executor, store).escalations())

    store2, engine2 = _store(tmp_path / "ten")
    for i in range(10):
        _seed_escalated_lease(store2, i, at=_NOW)
    ten = support.count_queries(engine2, lambda: _status_service(spawn_executor, store2).escalations())

    assert one == ten
    assert len(_status_service(spawn_executor, store2).escalations()) == 10


# --- open_takeovers_for / open_escalations_for ---------------------------------------------


def _open_takeover(store: SqlAlchemyRunnerStore, takeover_id: str, chunk_id: str, *, at: datetime) -> None:
    record_takeover_in_unit(
        store,
        takeover_id=takeover_id,
        chunk_id=chunk_id,
        lease_id=None,
        workdir=f"/ws/{chunk_id}",
        fence_epoch=None,
        opened_at=at,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{takeover_id}"),
        hold_epoch=0,  # held below the chunk's epoch 1, so an adopt is not held off by it
    )


def test_open_takeovers_for_matches_the_singular_and_drops_an_unknown_id(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _open_takeover(store, "tko_old", "ch_1", at=_NOW)
    _open_takeover(store, "tko_new", "ch_1", at=_NOW + timedelta(minutes=1))
    _open_takeover(store, "tko_2", "ch_2", at=_NOW)
    _open_takeover(store, "tko_ended", "ch_3", at=_NOW)
    store.record_takeover_end(takeover_id="tko_ended", ended_at=_NOW)

    result = store.open_takeovers_for(["ch_1", "ch_2", "ch_3", "ch_never"])

    assert set(result) == {"ch_1", "ch_2"}
    assert result["ch_1"].takeover_id == "tko_new"
    for chunk_id in ("ch_1", "ch_2"):
        assert result[chunk_id] == store.open_takeover_for_chunk(chunk_id)
    assert store.open_takeover_for_chunk("ch_3") is None


def test_open_takeovers_for_falls_back_to_the_older_takeover_once_the_newer_ends(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _open_takeover(store, "tko_old", "ch_1", at=_NOW)
    _open_takeover(store, "tko_new", "ch_1", at=_NOW + timedelta(minutes=1))
    store.record_takeover_end(takeover_id="tko_new", ended_at=_NOW)

    assert store.open_takeovers_for(["ch_1"])["ch_1"].takeover_id == "tko_old"


def test_open_escalations_for_matches_the_singular_and_drops_an_unknown_id(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _seed_escalated_lease(store, 1, at=_NOW)
    _seed_escalated_lease(store, 2, at=_NOW)
    _mint(store, "lease_active", "ch_active")

    result = store.open_escalations_for(["ch_1", "ch_2", "ch_active", "ch_never"])

    assert set(result) == {"ch_1", "ch_2"}
    for chunk_id in ("ch_1", "ch_2"):
        assert result[chunk_id] == store.open_escalation_for_chunk(chunk_id)


def test_the_takeover_and_escalation_plurals_match_across_a_batch_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    for i in range(7):
        _seed_escalated_lease(store, i, at=_NOW)
        _open_takeover(store, f"tko_{i}", f"ch_{i}", at=_NOW)
    ids = [f"ch_{i}" for i in range(7)]

    takeovers = store.open_takeovers_for(ids)
    escalations = store.open_escalations_for(ids)

    assert set(takeovers) == set(escalations) == set(ids)
    for chunk_id in ids:
        assert takeovers[chunk_id] == store.open_takeover_for_chunk(chunk_id)
        assert escalations[chunk_id] == store.open_escalation_for_chunk(chunk_id)


def test_the_takeover_and_escalation_plurals_issue_no_statement_for_no_ids(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)

    assert support.count_queries(engine, lambda: (store.open_takeovers_for([]), store.open_escalations_for([]))) == 0


def test_the_takeover_and_escalation_plurals_statement_count_is_flat_across_id_set_sizes(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    ids = [f"ch_{i}" for i in range(4)]
    for i in range(4):
        _seed_escalated_lease(store, i, at=_NOW)
        _open_takeover(store, f"tko_{i}", f"ch_{i}", at=_NOW)

    for plural in (store.open_takeovers_for, store.open_escalations_for):
        one = support.count_queries(engine, lambda plural=plural: plural(ids[:1]))
        four = support.count_queries(engine, lambda plural=plural: plural(ids))
        assert one == four, plural


# --- latest_epochs / leases_in_binding_tenure -------------------------------------------------


def _lease_at(store: SqlAlchemyRunnerStore, chunk_id: str, epoch: int, *, at: datetime) -> None:
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=f"lease_{chunk_id}_{epoch}",
                chunk_id=chunk_id,
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=epoch,
                retries_max=2,
                created_at=at,
            )
        )
    )


def _seed_epoch_chunks(store: SqlAlchemyRunnerStore, n: int) -> dict[str, datetime]:
    """``n`` chunks cycling through: a lease in tenure; a lease older than its binding; a lease behind a release
    floor; a lease above that floor; and a takeover fence with no lease. Returns each chunk's binding instant,
    plus one for a chunk the store has never heard of."""
    bounds: dict[str, datetime] = {"ch_never": _NOW}
    for i in range(n):
        chunk_id, kind = f"ch_{i}", i % 5
        bounds[chunk_id] = _NOW
        if kind == 4:
            record_takeover_in_unit(
                store,
                takeover_id=f"tko_{i}",
                chunk_id=chunk_id,
                lease_id=None,
                workdir=f"/ws/{chunk_id}",
                fence_epoch=9,
                opened_at=_NOW,
                session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{i}"),
                hold_epoch=0,
            )
            continue
        _lease_at(store, chunk_id, 1, at=_NOW - timedelta(minutes=1) if kind == 1 else _NOW)
        if kind in (2, 3):
            store.record_binding(chunk_id=chunk_id, environment_id=f"e{i}", workdir=f"/ws/e{i}", bound_at=_NOW)
            store.record_release(chunk_id=chunk_id, environment_id=f"e{i}", released_at=_NOW)
        if kind == 3:
            _lease_at(store, chunk_id, 2, at=_NOW)
    return bounds


def test_the_epoch_and_tenure_plurals_match_the_singulars_across_a_batch_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    bounds = _seed_epoch_chunks(store, 10)

    epochs = store.latest_epochs(bounds)
    in_tenure = store.leases_in_binding_tenure(bounds)

    assert "ch_never" not in epochs
    assert epochs["ch_4"] == 9
    assert in_tenure == {"ch_0", "ch_3", "ch_5", "ch_8"}
    for chunk_id, bound_at in bounds.items():
        assert epochs.get(chunk_id, 0) == store.latest_epoch(chunk_id)
        assert (chunk_id in in_tenure) is store.has_lease_in_binding_tenure(chunk_id, bound_at)


def test_the_epoch_and_tenure_plurals_issue_no_statement_for_no_ids(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)

    assert support.count_queries(engine, lambda: (store.latest_epochs([]), store.leases_in_binding_tenure({}))) == 0


def test_the_epoch_and_tenure_plurals_statement_count_is_flat_across_id_set_sizes(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    bounds = _seed_epoch_chunks(store, 5)
    one = {"ch_0": bounds["ch_0"]}

    for plural in (store.latest_epochs, store.leases_in_binding_tenure):
        few = support.count_queries(engine, lambda plural=plural: plural(one))
        many = support.count_queries(engine, lambda plural=plural: plural(bounds))
        assert few == many, plural


def test_the_epoch_and_tenure_plurals_rows_read_do_not_grow_with_lease_history(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    bounds = _seed_epoch_chunks(store, 5)

    def rows() -> list[int]:
        return [
            support.count_rows_read(engine, lambda plural=plural: plural(bounds))
            for plural in (store.latest_epochs, store.leases_in_binding_tenure)
        ]

    shallow = rows()
    for chunk_id in ("ch_0", "ch_1", "ch_3"):  # each already answers with a row
        for epoch in range(3, 13):
            _lease_at(store, chunk_id, epoch, at=_NOW)

    assert rows() == shallow


# --- InterruptedClaims.reconcile ------------------------------------------------------------


def _reconcile_statements(tmp_path: Path, n: int) -> list[str]:
    """The statements one reconcile issues over ``n`` held, lease-less chunks the hub reports RUNNING and routed
    here — each with an open takeover from an earlier epoch, so the adopt arm is reached, and an open escalation
    at the hub's epoch, so the adopt arm weighs it and leaves the entry to ADVANCE."""
    store, engine = _store(tmp_path)
    hub = FakeHub()
    for i in range(n):
        _seed_escalated_lease(store, i, at=_NOW)
        _open_takeover(store, f"tko_{i}", f"ch_{i}", at=_NOW)
        hub.chunks[f"ch_{i}"] = ChunkState(
            chunk_id=f"ch_{i}", status=ChunkStatus.RUNNING, route_runner_id="r1", latest_epoch=1
        )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({f"e{i}": f"/ws/e{i}" for i in range(n)}),
        harness=FakeHarness(
            handle=WorkerHandle(session_id="unused", pid=1, process_start_time="unused", pgid=1), verdict="pass"
        ),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )
    with support.capture_statements(engine) as statements:
        InterruptedClaims(ctx).reconcile()
    assert hub.claims == []  # nothing adopted, reclaimed or released
    return [statement for statement, _ in statements]


def test_reconcile_reads_takeovers_and_escalations_once_across_1_and_4_held_chunks(tmp_path: Path) -> None:
    """The takeover and escalation reads are one each, however many held chunks the sweep reaches."""
    for marker in ("takeover_ends", "escalation_closures"):
        one = [s for s in _reconcile_statements(tmp_path / f"{marker}1", 1) if marker in s]
        four = [s for s in _reconcile_statements(tmp_path / f"{marker}4", 4) if marker in s]
        assert len(one) == len(four) == 1, marker


def test_reconcile_issues_the_same_statement_count_across_1_and_4_chunks_held_in_adopt(tmp_path: Path) -> None:
    assert len(_reconcile_statements(tmp_path / "one", 1)) == len(_reconcile_statements(tmp_path / "four", 4)) == 8


def _closed_lease(store: SqlAlchemyRunnerStore, chunk_id: str, epoch: int, *, at: datetime) -> None:
    _lease_at(store, chunk_id, epoch, at=at)
    store.record_closure(
        lease_id=f"lease_{chunk_id}_{epoch}", chunk_id=chunk_id, node_id="nd_build", reason="failed", closed_at=at
    )


def test_reconcile_decides_each_held_chunk_by_its_own_epoch_and_binding_tenure(tmp_path: Path) -> None:
    """``ch_new``'s lease predates its binding and ``ch_kept`` holds the hub's epoch, so both adopt;
    ``ch_advance`` is in tenure behind the hub, so it is left."""
    store, _ = _store(tmp_path)
    earlier = _NOW - timedelta(minutes=10)
    hub = FakeHub()
    chunks = {  # chunk: (env, lease epoch, bound at, hub epoch)
        "ch_new": ("e0", 1, _NOW, 5),
        "ch_advance": ("e1", 1, _NOW - timedelta(minutes=20), 3),
        "ch_kept": ("e2", 3, _NOW - timedelta(minutes=20), 3),
    }
    for chunk_id, (env, epoch, bound_at, hub_epoch) in chunks.items():
        _closed_lease(store, chunk_id, epoch, at=earlier)
        store.record_binding(chunk_id=chunk_id, environment_id=env, workdir=f"/ws/{env}", bound_at=bound_at)
        store.set_route_token(chunk_id, token="rtok", at=_NOW)
        hub.chunks[chunk_id] = ChunkState(
            chunk_id=chunk_id, status=ChunkStatus.RUNNING, route_runner_id="r1", latest_epoch=hub_epoch
        )
        hub.envelopes[chunk_id] = make_envelope(
            chunk_id, "build", node_id="nd_build", choices=[("pass", "ok")], epoch=hub_epoch
        )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({f"e{i}": f"/ws/e{i}" for i in range(3)}),
        harness=FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    InterruptedClaims(ctx).reconcile()

    assert store.active_lease_chunk_ids() == {"ch_new", "ch_kept"}


# --- active_lease_chunk_ids -------------------------------------------------------------------


def test_active_lease_chunk_ids_names_every_open_lease_chunk(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _mint(store, "lease_1", "ch_1")
    _mint(store, "lease_2", "ch_2")
    _seed_escalated_lease(store, 3, at=_NOW)  # closed

    assert (
        store.active_lease_chunk_ids()
        == {lease.chunk_id for lease in store.list_active_leases()}
        == {
            "ch_1",
            "ch_2",
        }
    )
    assert support.count_queries(engine, store.active_lease_chunk_ids) == 1


# --- crash marking -------------------------------------------------------------------------


def _seed_crash_orphan(store: SqlAlchemyRunnerStore, i: int) -> None:
    """An active, spawned lease whose process is gone and whose last beat is long stale."""
    at = _NOW - timedelta(hours=1)
    lease_id, chunk_id = f"lease_{i}", f"ch_{i}"
    _mint(store, lease_id, chunk_id)
    session = SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{i}")
    store.record_spawn(lease_id, pid=2000 + i, process_start_time=f"t{i}", session=session, spawned_at=at)
    store.record_heartbeat(lease_id=lease_id, beat_at=at)


def _crash_marking_read_count(tmp_path: Path, n: int) -> tuple[int, int]:
    store, engine = _store(tmp_path)
    for i in range(n):
        _seed_crash_orphan(store, i)
    marked = 0

    def mark() -> None:
        nonlocal marked
        marked = ResumeIntents(make_stores(store)).mark_crashed(
            process=FakeProbe(alive=set()), liveness=store, now=_NOW
        )

    with support.capture_statements(engine) as statements:
        mark()
    reads = sum(1 for statement, _ in statements if statement.lstrip().upper().startswith("SELECT"))
    return marked, reads


def test_crash_marking_read_count_is_flat_across_1_and_5_orphaned_leases(tmp_path: Path) -> None:
    marked_one, one = _crash_marking_read_count(tmp_path / "one", 1)
    marked_five, five = _crash_marking_read_count(tmp_path / "five", 5)

    assert (marked_one, marked_five) == (1, 5)
    assert one == five


def test_crash_marking_read_count_grows_per_batch_not_per_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, unbatched = _crash_marking_read_count(tmp_path / "wide", 6)
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    marked, batched = _crash_marking_read_count(tmp_path / "narrow", 6)

    assert marked == 6
    # Two batches instead of one: the liveness read repeats per batch (three grouped reads each —
    # heartbeat, spawn, execution evidence).
    assert batched == unbatched + 3
