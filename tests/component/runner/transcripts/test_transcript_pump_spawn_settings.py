"""The transcript pump reads its segments' recorded spawn settings once per entry point: the statements touching
``lease_spawns`` stay flat as open segments and read windows grow, each segment still reads at the transcripts root its
own generation recorded, and a failed read is caught and marked per segment (component tier)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, TranscriptBatch
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.transcripts.ledger import TruncationReason
from blizzard.runner.transcripts.transcript_pump import TranscriptPump
from tests.support.runner_fakes import FakeTranscriptSource, SqlAlchemyRunnerStore
from tests.support.transcript_pump_world import (
    PUMP_NOW,
    lease_segment,
    mint_lease,
    one_turn_batches,
    pump_context,
    read_in_windows,
    statements_touching,
)


class _RootedSource(FakeTranscriptSource):
    """A scripted source that records the root each read was located at."""

    def __init__(self, batches_by_session: dict[str, TranscriptBatch]) -> None:
        super().__init__(batches_by_session=batches_by_session)
        self.roots: list[str] = []

    def at_root(self, root: str) -> IHarnessTranscriptSource:
        self.roots.append(root)
        return self


def _ctx(sessions: list[str]) -> tuple[LoopContext, _RootedSource, SqlAlchemyRunnerStore]:
    source = _RootedSource(one_turn_batches(sessions))
    ctx, store = pump_context(source)
    return ctx, source, store


def _open_segment(ctx: LoopContext, n: int, session: str, *, generations: int = 1) -> str:
    lease_id = f"lease_{n}"
    mint_lease(ctx, lease_id, f"ch_{n}")
    for generation in range(1, generations + 1):
        ctx.stores.liveness.record_spawn(
            lease_id,
            pid=generation,
            process_start_time=str(generation),
            session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
            spawned_at=PUMP_NOW + timedelta(seconds=generation),
            spawn_cwd=f"/ws/{n}",
            transcripts_root=f"/root/{n}/{generation}",
        )
    return lease_segment(ctx, lease_id)


def _world(n: int) -> tuple[LoopContext, _RootedSource, SqlAlchemyRunnerStore]:
    sessions = [f"sess-{i}" for i in range(n)]
    ctx, source, store = _ctx(sessions)
    for i in range(n):
        _open_segment(ctx, i, sessions[i])
    return ctx, source, store


def _raise(*_args: object) -> None:
    raise RuntimeError("store unavailable")


def test_run_spawn_settings_statements_are_flat_as_open_segments_grow() -> None:
    measured: list[int] = []
    for n in (1, 4):
        ctx, source, store = _world(n)
        measured.append(statements_touching(store, "lease_spawns", lambda ctx=ctx: TranscriptPump(ctx).run()))
        assert len(source.turns_since_calls) == n
    assert measured[0] == measured[1] == 1


def test_pump_lease_spawn_settings_statements_are_flat_as_other_open_segments_grow() -> None:
    measured: list[int] = []
    for n in (1, 4):
        ctx, _source, store = _world(n)
        measured.append(
            statements_touching(store, "lease_spawns", lambda ctx=ctx: TranscriptPump(ctx).pump_lease("lease_0"))
        )
    assert measured[0] == measured[1] == 1


def test_drain_segment_reads_spawn_settings_once_across_read_windows() -> None:
    measured: list[int] = []
    for windows in (1, 4):
        ctx, source, store = _ctx(["sess-0"])
        segment_id = _open_segment(ctx, 0, "sess-0")
        calls = read_in_windows(source, windows)
        measured.append(
            statements_touching(
                store,
                "lease_spawns",
                lambda ctx=ctx, segment_id=segment_id: TranscriptPump(ctx).drain_segment(
                    segment_id, deadline=PUMP_NOW + timedelta(hours=1)
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


def test_a_failed_batch_read_falls_back_to_each_segment_s_own_read(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, source, store = _world(2)
    monkeypatch.setattr(store, "spawn_settings_for", _raise)
    TranscriptPump(ctx).run()
    assert sorted(source.roots) == ["/root/0/1", "/root/1/1"]


def test_a_closing_lease_whose_spawn_settings_cannot_be_read_is_marked_not_sealed_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, source, store = _world(1)
    monkeypatch.setattr(store, "spawn_settings_for", _raise)
    monkeypatch.setattr(store, "spawn_settings", _raise)
    drained = TranscriptPump(ctx).pump_lease("lease_0", deadline=PUMP_NOW + timedelta(hours=1))
    [segment] = ctx.stores.transcript_ledger.open_transcript_segments_for_lease("lease_0")
    assert drained is False
    assert segment.truncated_reason == TruncationReason.LEASE_CLOSURE_INCOMPLETE
    assert source.turns_since_calls == []
