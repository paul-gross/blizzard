"""Runner-store read indexes (issue #520, component tier), mirroring
``tests/test_chunk_fact_table_indexes.py``'s shape: (a) five real reads each plan as an
index search off their own captured statement, never a table scan; (b) a plan-capture sweep
over a multi-tick loop run asserts no runner-store ``SELECT`` falls back to sqlite's own
``AUTOMATIC COVERING INDEX``. The replaced ``ix_transcript_segments_chunk_id`` is asserted
absent."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, event

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.tick import tick
from blizzard.wire.chunk import ChunkDetail
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.queue import QueuePeekEntry
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    SqlAlchemyRunnerStore,
    claimed_outcome,
    make_context,
    make_envelope,
    runner_store_errors,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
_ALIVE = (100, "start-100")  # (pid, start_time) for a running worker
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _migrated_engine(tmp_path: Path) -> Engine:
    config = runner_runtime.init_environment(tmp_path)
    return create_engine_from_url(config.db_url)


@contextmanager
def _capture_selects(engine: Engine) -> Iterator[list[tuple[str, Any]]]:
    """Every top-level ``SELECT`` the engine issues while the context is open, exactly as
    executed — statement text plus its bound parameters, ready to re-run under
    ``EXPLAIN QUERY PLAN`` with no drift from what the store actually sent."""
    statements: list[tuple[str, Any]] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _listener)


def _plan(engine: Engine, statement: str, parameters: Any) -> Sequence[sa.Row[Any]]:
    with engine.connect() as conn:
        return conn.exec_driver_sql(f"EXPLAIN QUERY PLAN {statement}", parameters).all()


def _one(statements: list[tuple[str, Any]]) -> tuple[str, Any]:
    assert len(statements) == 1, statements
    return statements[0]


# --- (a) the five #520 reads, each proven against its own real statement ----------------


def test_heartbeat_staleness_probe_plans_as_an_index_search(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)

    with _capture_selects(engine) as statements:
        store.latest_heartbeat("lease_1")

    plan = _plan(engine, *_one(statements))
    assert any("ix_heartbeats_lease_id_beat_at" in str(row) for row in plan), plan


def test_pending_outbound_plans_as_an_index_search(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)

    with _capture_selects(engine) as statements:
        store.pending_outbound()

    plan = _plan(engine, *_one(statements))
    assert any("ix_outbound_buffer_acked_at_seq" in str(row) for row in plan), plan


def test_pending_submission_lease_ids_plans_as_an_index_search(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.enqueue_outbound(
        kind="completion.submitted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW
    )

    with _capture_selects(engine) as statements:
        store.pending_submission_lease_ids()

    plan = _plan(engine, *_one(statements))
    assert any("ix_outbound_buffer_acked_at_seq" in str(row) for row in plan), plan


def test_attachments_for_lease_plans_as_an_index_search(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.record_attachment(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", epoch=1, name="asset", content="x", attached_at=_NOW
    )

    with _capture_selects(engine) as statements:
        store.attachments_for_lease("lease_1")

    plan = _plan(engine, *_one(statements))
    assert any("ix_attachments_lease_id_name_id" in str(row) for row in plan), plan


def test_held_binding_plans_as_an_index_search(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    with _capture_selects(engine) as statements:
        store.held_environment_ids()

    plan = _plan(engine, *_one(statements))
    assert any("ix_binding_releases_chunk_id_environment_id_released_at" in str(row) for row in plan), plan


# --- the replaced index ------------------------------------------------------------------


def test_ix_transcript_segments_chunk_id_is_absent(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    names = {i["name"] for i in sa.inspect(engine).get_indexes("transcript_segments")}
    assert "ix_transcript_segments_chunk_id" not in names
    assert "ix_transcript_segments_chunk_id_stamped_at_segment_id" in names


# --- (b) the plan-capture sweep -----------------------------------------------------------


def test_runner_loop_scenario_never_plans_an_automatic_covering_index(tmp_path: Path) -> None:
    """Drives the happy-path claim -> spawn -> exit -> judge -> flush -> hub-node-poll shape
    against a real migrated store and checks every distinct ``SELECT`` issued along the way."""
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    hub = FakeHub()
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    hub.envelopes["ch_1"] = env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN)]
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    wt = FakeWorktreeGit()
    probe = FakeProbe(alive={_ALIVE})  # worker alive during tick 1
    clock = FixedClock(_NOW)
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe, worktree_git=wt, clock=clock)

    with _capture_selects(engine) as statements:
        tick(ctx)  # FILL claims + spawns; the worker is alive
        probe.alive.clear()  # the worker finishes and exits
        tick(ctx)  # PULL flushes lease.minted; ADVANCE launches the detached elicitation
        tick(ctx)  # ADVANCE collects the elicitation and buffers the completion
        tick(ctx)  # PULL flushes the completion -> deliver hub node; envs held
        hub.chunks["ch_1"] = ChunkDetail(
            chunk_id="ch_1", graph_id="gr_1", status=ChunkStatus.DONE, current_node_id="deliver", latest_epoch=1
        )
        hub.queue = []
        tick(ctx)  # the hub-node poll sees `done` and releases the environment

    assert statements  # the scenario actually read from the store

    offenders: list[tuple[str, Sequence[sa.Row[Any]]]] = []
    seen: set[str] = set()
    for statement, parameters in statements:
        if statement in seen:
            continue
        seen.add(statement)
        plan = _plan(engine, statement, parameters)
        if any("AUTOMATIC COVERING INDEX" in str(row) for row in plan):
            offenders.append((statement, plan))
    assert not offenders, offenders
