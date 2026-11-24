"""The transcript pump reads its segments' recorded spawn settings once per entry point: the statements touching
``lease_spawns`` stay flat as open segments and read windows grow, and each segment still reads at the transcripts root
its own generation recorded (component tier)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, TranscriptBatch, TranscriptPosition
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.transcripts.transcript_pump import TranscriptPump
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    SqlAlchemyRunnerStore,
    make_context,
    make_store,
)
from tests.support.runner_mint import new_mint
from tests.support.scaffolding import capture_statements
from tests.support.transcript_batches import incomplete_batch, normalized_turn, transcript_batch

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


class _RootedSource(FakeTranscriptSource):
    """A scripted source that records the root each read was located at."""

    def __init__(self, batches_by_session: dict[str, TranscriptBatch]) -> None:
        super().__init__(batches_by_session=batches_by_session)
        self.roots: list[str] = []

    def at_root(self, root: str) -> IHarnessTranscriptSource:
        self.roots.append(root)
        return self


def _ctx(sessions: list[str]) -> tuple[LoopContext, _RootedSource, SqlAlchemyRunnerStore]:
    store = make_store("sqlite://")
    source = _RootedSource({s: transcript_batch([normalized_turn(0, "hi")], next_token="p") for s in sessions})
    harness = FakeHarness(
        handle=WorkerHandle(session_id="s", pid=1, process_start_time="1", pgid=1),
        verdict=None,
        transcript_source=source,
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_name="r1", runner_id="rn_1", transcripts_ship=True),
    )
    return ctx, source, store


def _open_segment(ctx: LoopContext, n: int, session: str, *, generations: int = 1) -> str:
    lease_id = f"lease_{n}"
    ctx.stores.lease_record.record_mint(
        new_mint(
            NewLease(
                lease_id=lease_id,
                chunk_id=f"ch_{n}",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=_NOW,
            )
        )
    )
    for generation in range(1, generations + 1):
        ctx.stores.liveness.record_spawn(
            lease_id,
            pid=generation,
            process_start_time=str(generation),
            session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
            spawned_at=_NOW,
            spawn_cwd=f"/ws/{n}",
            transcripts_root=f"/root/{n}/{generation}",
        )
    return next(s.segment_id for s in ctx.stores.transcript_ledger.open_transcript_segments() if s.lease_id == lease_id)


def _spawn_statements(store: SqlAlchemyRunnerStore, fn: Callable[[], object]) -> int:
    with capture_statements(store.engine) as statements:
        fn()
    return sum(1 for statement, _ in statements if "lease_spawns" in statement)


def _world(n: int) -> tuple[LoopContext, _RootedSource, SqlAlchemyRunnerStore]:
    sessions = [f"sess-{i}" for i in range(n)]
    ctx, source, store = _ctx(sessions)
    for i in range(n):
        _open_segment(ctx, i, sessions[i])
    return ctx, source, store


def test_run_spawn_settings_statements_are_flat_as_open_segments_grow() -> None:
    measured: list[int] = []
    for n in (1, 4):
        ctx, source, store = _world(n)
        measured.append(_spawn_statements(store, lambda ctx=ctx: TranscriptPump(ctx).run()))
        assert len(source.turns_since_calls) == n
    assert measured[0] == measured[1] == 1


def test_pump_lease_spawn_settings_statements_are_flat_as_other_open_segments_grow() -> None:
    measured: list[int] = []
    for n in (1, 4):
        ctx, _source, store = _world(n)
        measured.append(_spawn_statements(store, lambda ctx=ctx: TranscriptPump(ctx).pump_lease("lease_0")))
    assert measured[0] == measured[1] == 1


def test_drain_segment_reads_spawn_settings_once_across_read_windows() -> None:
    measured: list[int] = []
    for windows in (1, 4):
        ctx, source, store = _ctx(["sess-0"])
        segment_id = _open_segment(ctx, 0, "sess-0")
        calls = {"n": 0}

        def windowed(
            session_id: str,
            *,
            spawn_cwd: str | None,
            since: TranscriptPosition | None,
            calls: dict[str, int] = calls,
            windows: int = windows,
        ) -> TranscriptBatch:
            calls["n"] += 1
            token = f"p{calls['n']}"
            if calls["n"] < windows:
                return incomplete_batch([normalized_turn(calls["n"], "more")], next_token=token)
            return transcript_batch([normalized_turn(calls["n"], "end")], next_token=token)

        source.turns_since = windowed
        measured.append(
            _spawn_statements(
                store,
                lambda ctx=ctx, segment_id=segment_id: TranscriptPump(ctx).drain_segment(
                    segment_id, deadline=_NOW + timedelta(hours=1)
                ),
            )
        )
        assert calls["n"] == windows
    assert measured[0] == measured[1] == 1


def test_each_segment_reads_at_its_own_generation_s_recorded_root() -> None:
    ctx, source, _store = _ctx(["sess-0", "sess-1"])
    _open_segment(ctx, 0, "sess-0", generations=1)
    _open_segment(ctx, 1, "sess-1", generations=3)
    TranscriptPump(ctx).run()
    assert sorted(source.roots) == ["/root/0/1", "/root/1/3"]
