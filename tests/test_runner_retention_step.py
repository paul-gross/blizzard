"""``Retention`` — the tick's append-only-lane pruner (issue #520).

Each of outbound/heartbeat/external-usage-sample retention is its own store-level
derivation, already proven against a real store in ``tests/test_runner_store.py``. This
file pins only the step's own contract over that: it calls every lane every tick, and one
lane's prune raising never costs the others theirs. The worker-stdout lane (issue #58) is
filesystem- rather than store-backed, so its own age-based sweep is proven directly here
too, rather than in ``tests/test_runner_store.py``.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import Retention
from blizzard.runner.store.schema import external_usage_samples, heartbeats
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_store

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-1", pid=100, process_start_time="start-100", pgid=100)


class _RaisingOnCall:
    """Wraps a real store, raising when one named method is called — every other
    attribute (including every other method) passes straight through to ``inner``. Lets a
    test isolate exactly one lane's prune failure without faking the other two."""

    def __init__(self, inner: object, method: str) -> None:
        self._inner = inner
        self._method = method

    def __getattr__(self, name: str) -> object:
        if name == self._method:

            def _raise(*args: object, **kwargs: object) -> object:
                raise RuntimeError(f"{name} failed (scripted)")

            return _raise
        return getattr(self._inner, name)


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _ctx(store, *, config: LoopConfig | None = None):  # type: ignore[no-untyped-def]
    return make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=config or LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1),
    )


def _seed_a_stale_and_a_pending_outbound_fact(store, *, old: datetime) -> int:  # type: ignore[no-untyped-def]
    """A pending row (the floor) plus an acked, aged one below it — the ordinary
    prunable case (mirrors ``tests/test_runner_store.py``'s own outbound retention tests)."""
    stale = store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=old)
    store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=old)
    store.ack_outbound(stale, acked_at=old)
    return stale


def _seed_a_superseded_and_a_newest_heartbeat(store, *, old: datetime) -> None:  # type: ignore[no-untyped-def]
    store.record_heartbeat(lease_id="lease_1", beat_at=old)
    store.record_heartbeat(lease_id="lease_1", beat_at=old + timedelta(minutes=1))


def _seed_a_superseded_and_a_newest_usage_sample(store, *, old: datetime) -> None:  # type: ignore[no-untyped-def]
    store.record_external_usage_attempt(
        slug="anthropic", sampled_at=old, payload=None, report_kind="", report_payload=""
    )
    store.record_external_usage_attempt(
        slug="anthropic", sampled_at=old + timedelta(minutes=1), payload=None, report_kind="", report_payload=""
    )


def _heartbeat_row_count(store) -> int:  # type: ignore[no-untyped-def]
    with store._engine.connect() as conn:
        return int(
            conn.execute(
                sa.select(sa.func.count()).select_from(heartbeats).where(heartbeats.c.lease_id == "lease_1")
            ).scalar_one()
        )


def _usage_sample_row_count(store) -> int:  # type: ignore[no-untyped-def]
    with store._engine.connect() as conn:
        return int(
            conn.execute(
                sa.select(sa.func.count())
                .select_from(external_usage_samples)
                .where(external_usage_samples.c.slug == "anthropic")
            ).scalar_one()
        )


@pytest.mark.unit
def test_retention_prunes_all_three_lanes_every_tick(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    old = _NOW - timedelta(days=8)  # past every lane's own retention window
    stale_outbound_seq = _seed_a_stale_and_a_pending_outbound_fact(store, old=old)
    _seed_a_superseded_and_a_newest_heartbeat(store, old=old)
    _seed_a_superseded_and_a_newest_usage_sample(store, old=old)
    ctx = _ctx(store)

    Retention(ctx).run()

    assert stale_outbound_seq not in {f.seq for f in store.recent_outbound(10)}
    assert _heartbeat_row_count(store) == 1
    assert _usage_sample_row_count(store) == 1


@pytest.mark.unit
def test_one_lanes_prune_failure_does_not_cost_the_others_theirs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A diagnostic-adjacent lane must never be able to end the sweep it rides in. The
    liveness store raises on ``prune_heartbeats``; the assertion that matters is the OTHER
    two lanes' prunes still landing, which a total-abort implementation would not satisfy."""
    store = _store(tmp_path)
    old = _NOW - timedelta(days=8)
    stale_outbound_seq = _seed_a_stale_and_a_pending_outbound_fact(store, old=old)
    _seed_a_superseded_and_a_newest_heartbeat(store, old=old)
    _seed_a_superseded_and_a_newest_usage_sample(store, old=old)
    ctx = _ctx(store)
    failing_liveness = _RaisingOnCall(ctx.stores.liveness, "prune_heartbeats")
    ctx = dataclasses.replace(ctx, stores=dataclasses.replace(ctx.stores, liveness=failing_liveness))

    Retention(ctx).run()  # must not raise

    assert stale_outbound_seq not in {f.seq for f in store.recent_outbound(10)}  # outbound still pruned
    assert _usage_sample_row_count(store) == 1  # usage still pruned
    assert _heartbeat_row_count(store) == 2  # the raising lane's own prune never landed


@pytest.mark.unit
def test_retention_sweeps_worker_stdout_and_stderr_past_the_retention_window(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Both streams, past a configured retention window, are pruned; anything inside the
    window — including a still-fresh file from an unrelated lease/generation — is left."""
    store = _store(tmp_path)
    stdout_dir = tmp_path / "worker-stdout"
    stdout_dir.mkdir()
    old_stdout = stdout_dir / "lease_a.1.stdout"
    old_stderr = stdout_dir / "lease_a.1.stderr"
    fresh_stdout = stdout_dir / "lease_b.1.stdout"
    for f in (old_stdout, old_stderr, fresh_stdout):
        f.write_text("envelope")
    stale_mtime = (_NOW - timedelta(days=15)).timestamp()  # past the 14-day default
    os.utime(old_stdout, (stale_mtime, stale_mtime))
    os.utime(old_stderr, (stale_mtime, stale_mtime))
    config = LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, worker_stdout_dir=str(stdout_dir))
    ctx = _ctx(store, config=config)

    Retention(ctx).run()

    assert not old_stdout.exists()
    assert not old_stderr.exists()
    assert fresh_stdout.exists()


@pytest.mark.unit
def test_worker_stdout_sweep_failure_does_not_cost_the_others_theirs(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    old = _NOW - timedelta(days=8)
    stale_outbound_seq = _seed_a_stale_and_a_pending_outbound_fact(store, old=old)
    _seed_a_superseded_and_a_newest_heartbeat(store, old=old)
    _seed_a_superseded_and_a_newest_usage_sample(store, old=old)
    ctx = _ctx(store)
    failing_worker_files = _RaisingOnCall(ctx.worker_files, "sweep")
    ctx = dataclasses.replace(ctx, worker_files=failing_worker_files)

    Retention(ctx).run()  # must not raise

    assert stale_outbound_seq not in {f.seq for f in store.recent_outbound(10)}  # outbound still pruned
    assert _heartbeat_row_count(store) == 1  # heartbeat still pruned
    assert _usage_sample_row_count(store) == 1  # usage still pruned
