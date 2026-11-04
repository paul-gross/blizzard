"""The runner adheres to the hub's pause brake — loop component tier.

The declarative pause brake lives at the hub; the runner reads it on PULL, mirrors it
to its store, and FILL adheres: paused = no new claims, in-flight chunks run on. When
the hub is unreachable the runner keeps its last-mirrored directive. Driven against a
real (tmp sqlite) runner store with fakes standing in only at the seams."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.fact_kinds import (
    RUNNER_LOCALLY_PAUSED,
    RUNNER_LOCALLY_RESUMED,
)
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.node_steps.chunk_state import ChunkState
from tests.runner_fakes import (
    SqlAlchemyRunnerStore,
    make_store,
    record_usage,
    serving,
)

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path: Path):  # pyright: ignore[reportUnusedFunction]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _pause_locally(store: SqlAlchemyRunnerStore, ctx: LoopContext, *, paused: bool):  # pyright: ignore[reportUnusedFunction]
    """Set the runner's own brake, the way `PATCH /runner` does — fact + report, one write."""
    store.record_local_pause(
        paused=paused,
        at=ctx.clock.now(),
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED if paused else RUNNER_LOCALLY_RESUMED,
        report_payload=json.dumps({"by": "operator"}),
    )


def _seed_running_lease(  # pyright: ignore[reportUnusedFunction]
    store: SqlAlchemyRunnerStore,
    *,
    chunk: str = "ch_1",
    lease: str = "lease_1",
    pid: int = 100,
    start: str = "start-100",
    session: str = "sess-a",
    epoch: int = 1,
):
    """A build lease spawned into env e1 with a live worker, plus its binding."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=pid,
        process_start_time=start,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
        spawned_at=_NOW,
        pgid=pid,
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")


def _running_chunk(chunk: str = "ch_1", *, runner_id: str = "r1") -> ChunkState:  # pyright: ignore[reportUnusedFunction]
    return ChunkState(
        chunk_id=chunk,
        status=ChunkStatus.RUNNING,
        latest_epoch=1,
        route_runner_id=runner_id,
    )


def _ceiling_config(cap: float | None, *, window_hours: float = 24.0, agents: int = 1) -> LoopConfig:  # pyright: ignore[reportUnusedFunction]
    return LoopConfig(
        runner_name="r1",
        runner_id="rn_1",
        bindings=serving(agents),
        agents=agents,
        runner_ceiling_usd=cap,
        runner_ceiling_window_hours=window_hours,
    )


def _sample(*, cost: float | None, kind: UsageKind = "spawn") -> UsageSample:
    return UsageSample(
        kind=kind,
        model="claude-x",
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=cost,
    )


def _record_usage(  # pyright: ignore[reportUnusedFunction]
    store: SqlAlchemyRunnerStore,
    *,
    lease_id: str = "lease_1",
    chunk_id: str = "ch_1",
    cost: float | None,
    recorded_at: datetime,
) -> None:
    record_usage(
        store,
        lease_id=lease_id,
        chunk_id=chunk_id,
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=_sample(cost=cost),
        recorded_at=recorded_at,
    )
