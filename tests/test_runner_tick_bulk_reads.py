"""The runner tick's bulk-read seams — REAP, FILL, ADVANCE, ContextSample, and
``backing_off_facts``, each collapsed from a per-lease/per-fact read to a plural keyed by
the id set the caller already holds (`bzh:bulk-reconstitution`).

Two shapes per new plural: it matches its singular sibling and drops an unknown id, and its
own statement count is flat across a lowered ``BATCH_SIZE`` boundary. The tick tests drive a
full ``tick(ctx)`` at N=1 and N=10 steady-state leases and pin the statement count equal —
the test the work item calls out as mattering more than any single fix. The last section does
the same for ``RunnerStatusService.escalations()`` (runner-API read path)."""

from __future__ import annotations

from concurrent.futures import Executor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store import batching as batching_module
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.domain.overload import backing_off_facts
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.adapter import ClaudeCodeAdapter
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.tick import tick
from tests import support
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    SqlAlchemyRunnerStore,
    make_context,
    runner_store_errors,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)


def _migrated_engine(tmp_path: Path) -> Engine:
    config = runner_runtime.init_environment(tmp_path)
    return create_engine_from_url(config.db_url)


def _store(tmp_path: Path) -> tuple[SqlAlchemyRunnerStore, Engine]:
    engine = _migrated_engine(tmp_path)
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
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )


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


def _seed_steady_state_lease(store: SqlAlchemyRunnerStore, i: int, *, at: datetime, sampled_at: datetime) -> None:
    """One active, spawned, beating, bound lease, sampled at ``sampled_at`` — sampled at ``at``
    nothing about it is due for any per-tick action, so a flat tick issues the same
    statements regardless of i."""
    lease_id, chunk_id, session_id = f"lease_{i}", f"ch_{i}", f"sess-{i}"
    session = SessionReference(CLAUDE_CODE_HARNESS_ID, session_id)
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=at,
        )
    )
    store.record_spawn(lease_id, pid=1000 + i, process_start_time=f"t{i}", session=session, spawned_at=at)
    store.record_heartbeat(lease_id=lease_id, beat_at=at)
    store.record_binding(chunk_id=chunk_id, environment_id=f"e{i}", workdir=f"/ws/e{i}", bound_at=at)
    store.record_context_sample(
        lease_id=lease_id, chunk_id=chunk_id, context_tokens=100, sampled_at=sampled_at, session=session
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
        store.record_binding(chunk_id=f"ch_held_{j}", environment_id=f"eh{j}", workdir=f"/ws/eh{j}", bound_at=_NOW)
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
            runner_id="r1",
            workspace_id="ws1",
            max_agents=n,
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


def test_a_full_tick_adds_one_statement_per_lease_when_every_lease_is_due_for_a_sample(tmp_path: Path) -> None:
    """The sample INSERT is legitimately per lease; the binding read that locates its transcript
    must not be a second one."""
    one = _tick_statement_count(tmp_path / "n1", 1, due=True)
    ten = _tick_statement_count(tmp_path / "n10", 10, due=True)

    assert ten - one == 9


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
        lease_record=store,
        outbound=store,
        environments=store,
        asks=store,
        takeover=store,
        escalations=store,
        runner_id="r1",
        workspace_id="ws1",
        max_agents=10,
        hub_url="http://hub",
        env_pool=(),
        workspace_root="/ws",
        harnesses=HarnessRegistry(
            {
                CLAUDE_CODE_HARNESS_ID: HarnessBinding(
                    adapter=ClaudeCodeAdapter(
                        worker_env=AllowlistedEnv.of(()),
                        binary="claude",
                        permission_mode="bypassPermissions",
                        process=probe,
                        launcher=ProcessLauncher(probe, executor=spawn_executor),
                    )
                )
            }
        ),
    )


def _seed_escalated_lease(store: SqlAlchemyRunnerStore, i: int, *, at: datetime) -> None:
    lease_id, chunk_id, session_id = f"lease_{i}", f"ch_{i}", f"sess-{i}"
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=at,
        )
    )
    store.record_spawn(
        lease_id,
        pid=1000 + i,
        process_start_time=f"t{i}",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session_id),
        spawned_at=at,
    )
    store.record_binding(chunk_id=chunk_id, environment_id=f"e{i}", workdir=f"/ws/e{i}", bound_at=at)
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
