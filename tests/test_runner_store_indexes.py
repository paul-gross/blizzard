"""Runner-store read indexes (issue #520, component tier), mirroring
``tests/test_chunk_fact_table_indexes.py``'s shape: (a) five real reads each plan as an
index search off their own captured statement, never a table scan; (b) a plan-capture sweep
over a multi-tick loop run asserts no runner-store ``SELECT`` falls back to sqlite's own
``AUTOMATIC COVERING INDEX``. The replaced ``ix_transcript_segments_chunk_id`` is asserted
absent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.tick import tick
from blizzard.wire.chunk import ChunkStatusView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.queue import QueuePeekEntry
from tests import support
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
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _migrated_engine(tmp_path: Path) -> Engine:
    config = runner_runtime.init_environment(tmp_path)
    return create_engine_from_url(config.db_url)


def _one_select(statements: list[tuple[str, Any]]) -> tuple[str, Any]:
    """The five single-index tests each capture around one store-method call; filter to the
    one ``SELECT`` among any writes the method also issues (``capture_statements`` records
    every statement, not just reads)."""
    selects = [s for s in statements if s[0].lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, statements
    return selects[0]


# --- (a) the five #520 reads, each proven against its own real statement ----------------


def test_heartbeat_staleness_probe_plans_as_an_index_search(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)

    with support.capture_statements(engine) as statements:
        store.latest_heartbeat("lease_1")

    plan = support.explain_query_plan(engine, *_one_select(statements))
    assert any("ix_heartbeats_lease_id_beat_at" in str(row) for row in plan), plan


def test_pending_outbound_plans_as_an_index_search(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)

    with support.capture_statements(engine) as statements:
        store.pending_outbound()

    plan = support.explain_query_plan(engine, *_one_select(statements))
    assert any("ix_outbound_buffer_acked_at_seq" in str(row) for row in plan), plan


def test_pending_submission_lease_ids_plans_as_an_index_search(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.enqueue_outbound(
        kind="completion.submitted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW
    )

    with support.capture_statements(engine) as statements:
        store.pending_submission_lease_ids()

    plan = support.explain_query_plan(engine, *_one_select(statements))
    assert any("ix_outbound_buffer_acked_at_seq" in str(row) for row in plan), plan


def test_attachments_for_lease_plans_as_an_index_search(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.record_attachment(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", epoch=1, name="asset", content="x", attached_at=_NOW
    )

    with support.capture_statements(engine) as statements:
        store.attachments_for_lease("lease_1")

    plan = support.explain_query_plan(engine, *_one_select(statements))
    assert any("ix_attachments_lease_id_name_id" in str(row) for row in plan), plan


def test_held_binding_plans_as_an_index_search(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    with support.capture_statements(engine) as statements:
        store.held_environment_ids()

    plan = support.explain_query_plan(engine, *_one_select(statements))
    assert any("ix_binding_releases_chunk_id_environment_id_released_at" in str(row) for row in plan), plan


# --- the replaced index ------------------------------------------------------------------


def test_ix_transcript_segments_chunk_id_is_absent(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    names = {i["name"] for i in sa.inspect(engine).get_indexes("transcript_segments")}
    assert "ix_transcript_segments_chunk_id" not in names
    assert "ix_transcript_segments_chunk_id_stamped_at_segment_id" in names


# --- support.capture_statements / count_queries themselves ------------------------------


def test_capture_statements_records_the_exact_statement_and_count_for_one_store_read(tmp_path: Path) -> None:
    """Reuses the heartbeat read (a) above already proves plans as an index search — here
    the point is the shared helper's own contract: one statement captured, and it's really
    the ``heartbeats`` ``SELECT`` the store issued, not just a count."""
    engine = _migrated_engine(tmp_path)
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)

    with support.capture_statements(engine) as statements:
        store.latest_heartbeat("lease_1")

    assert len(statements) == 1, statements
    statement, parameters = statements[0]
    assert statement.lstrip().upper().startswith("SELECT")
    assert "heartbeats" in statement
    assert "lease_1" in parameters

    assert support.count_queries(engine, lambda: store.latest_heartbeat("lease_1")) == 1


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

    with support.capture_statements(engine) as statements:
        tick(ctx)  # FILL claims + spawns; the worker is alive
        probe.alive.clear()  # the worker finishes and exits
        tick(ctx)  # PULL flushes lease.minted; ADVANCE launches the detached elicitation
        tick(ctx)  # ADVANCE collects the elicitation and buffers the completion
        tick(ctx)  # PULL flushes the completion -> deliver hub node; envs held
        hub.chunks["ch_1"] = ChunkStatusView(chunk_id="ch_1", status=ChunkStatus.DONE, latest_epoch=1)
        hub.queue = []
        tick(ctx)  # the hub-node poll sees `done` and releases the environment

    assert statements  # the scenario actually read from the store

    # Every table, not a declared allow-list (that's the gate's job) — small singleton tables legitimately plan as
    # a bare scan, so only sqlite's automatic covering index is a sweep-test offense here.
    tables = sa.inspect(engine).get_table_names()
    offenders = [
        (table, row)
        for table, row in support.offending_index_scans(engine, statements, tables)
        if "AUTOMATIC" in row.detail
    ]
    assert not offenders, offenders
