"""The transcript pump resolves its environment bindings once per entry point for the chunks it will pump: the
statements touching ``env_bindings`` stay flat as released segments and read windows grow, and each chunk still reads
in the directory its binding names (component tier)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.transcript import TranscriptBatch
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.transcripts.transcript_pump import TranscriptPump
from tests.support.runner_fakes import FakeTranscriptSource, SqlAlchemyRunnerStore
from tests.support.scaffolding import count_rows_read
from tests.support.transcript_pump_world import (
    PUMP_NOW,
    lease_segment,
    mint_lease,
    one_turn_batches,
    pump_context,
    read_in_windows,
    statements_touching,
)


def _ctx(
    sessions: list[str], *, batches: dict[str, TranscriptBatch] | None = None
) -> tuple[LoopContext, FakeTranscriptSource, SqlAlchemyRunnerStore]:
    source = FakeTranscriptSource(batches_by_session=batches or one_turn_batches(sessions))
    ctx, store = pump_context(source)
    return ctx, source, store


def _open_segment(
    ctx: LoopContext, chunk_id: str, session: str, *, held: bool, bind: bool = True, spawn_cwd: str | None = None
) -> str:
    n = chunk_id.removeprefix("ch_")
    if bind:
        ctx.stores.environments.record_binding(
            chunk_id=chunk_id, environment_id=f"env-{n}", workdir=f"/ws/{n}", bound_at=PUMP_NOW, workspace_id=None
        )
    mint_lease(ctx, f"lease_{n}", chunk_id)
    ctx.stores.liveness.record_spawn(
        f"lease_{n}",
        pid=1,
        process_start_time="1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
        spawned_at=PUMP_NOW,
        spawn_cwd=spawn_cwd,
    )
    if bind and not held:
        ctx.stores.environments.record_release(chunk_id=chunk_id, environment_id=f"env-{n}", released_at=PUMP_NOW)
    return lease_segment(ctx, f"lease_{n}")


def _binding_statements(store: SqlAlchemyRunnerStore, fn: Callable[[], object]) -> int:
    return statements_touching(store, "env_bindings", fn)


def _world(n: int, *, held: bool) -> tuple[LoopContext, FakeTranscriptSource, SqlAlchemyRunnerStore, list[str]]:
    sessions = [f"sess-{i}" for i in range(n)]
    ctx, source, store = _ctx(sessions)
    segment_ids = [_open_segment(ctx, f"ch_{i}", sessions[i], held=held) for i in range(n)]
    return ctx, source, store, segment_ids


@pytest.mark.parametrize("held", [True, False])
def test_run_binding_statements_are_flat_as_segments_grow(held: bool) -> None:
    measured: list[int] = []
    for n in (1, 4):
        ctx, _source, store, _ids = _world(n, held=held)
        measured.append(_binding_statements(store, lambda ctx=ctx: TranscriptPump(ctx).run()))
    assert measured[0] == measured[1]


@pytest.mark.parametrize("held", [True, False])
def test_pump_lease_binding_statements_are_flat_as_other_open_segments_grow(held: bool) -> None:
    measured: list[int] = []
    for n in (1, 4):
        ctx, _source, store, _ids = _world(n, held=held)
        measured.append(_binding_statements(store, lambda ctx=ctx: TranscriptPump(ctx).pump_lease("lease_0")))
    assert measured[0] == measured[1]


def test_drain_segment_resolves_bindings_once_across_read_windows() -> None:
    measured: list[int] = []
    for windows in (1, 4):
        ctx, source, store = _ctx(["sess-0"])
        segment_id = _open_segment(ctx, "ch_0", "sess-0", held=False)
        calls = read_in_windows(source, windows)
        measured.append(
            _binding_statements(
                store,
                lambda ctx=ctx, segment_id=segment_id: TranscriptPump(ctx).drain_segment(
                    segment_id, deadline=PUMP_NOW + timedelta(hours=1)
                ),
            )
        )
        assert calls["n"] == windows
    assert measured[0] == measured[1]


def test_run_rows_read_from_bindings_are_flat_as_unrelated_held_bindings_grow() -> None:
    measured: list[int] = []
    for unrelated in (0, 6):
        ctx, _source, store = _ctx(["sess-0"])
        _open_segment(ctx, "ch_0", "sess-0", held=True)
        for i in range(unrelated):
            ctx.stores.environments.record_binding(
                chunk_id=f"ch_other{i}", environment_id=f"env-o{i}", workdir=f"/ws/o{i}", bound_at=PUMP_NOW
            )
        measured.append(count_rows_read(store.engine, lambda ctx=ctx: TranscriptPump(ctx).run()))
    assert measured[0] == measured[1]


def test_a_segment_without_a_frozen_cwd_reads_in_the_directory_its_newest_binding_names() -> None:
    ctx, source, _store = _ctx(["sess-held", "sess-released", "sess-unbound"])
    _open_segment(ctx, "ch_1", "sess-held", held=True)
    _open_segment(ctx, "ch_2", "sess-released", held=False)
    _open_segment(ctx, "ch_3", "sess-unbound", held=False, bind=False)
    TranscriptPump(ctx).run()
    cwds = {session: cwd for session, cwd, _ in source.turns_since_calls}
    assert cwds["sess-held"] == "/ws/1"
    assert cwds["sess-released"] == "/ws/2"  # a released newest binding still names the workdir it was saved with
    assert cwds["sess-unbound"] is None
    assert len(cwds) == 3


def test_a_segment_with_a_frozen_spawn_cwd_reads_in_that_directory() -> None:
    ctx, source, _store = _ctx(["sess-held", "sess-released"])
    _open_segment(ctx, "ch_1", "sess-held", held=True, spawn_cwd="/frozen/1")
    _open_segment(ctx, "ch_2", "sess-released", held=False, spawn_cwd="/frozen/2")
    TranscriptPump(ctx).run()
    assert {session: cwd for session, cwd, _ in source.turns_since_calls} == {
        "sess-held": "/frozen/1",
        "sess-released": "/frozen/2",
    }
