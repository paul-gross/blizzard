"""The transcript pump resolves its environment bindings once per entry point for the chunks it will pump: the
statements touching ``env_bindings`` stay flat as released segments and read windows grow, and each chunk still reads
in the directory its binding names (component tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.transcripts.transcript_pump import TranscriptPump
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    SqlAlchemyRunnerStore,
    make_context,
    make_store,
)
from tests.support import capture_statements, count_rows_read
from tests.test_transcript_pump import incomplete_batch, normalized_turn, transcript_batch

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _ctx(sessions: list[str], *, batches=None):
    store = make_store("sqlite://")
    source = FakeTranscriptSource(
        batches_by_session=batches
        or {s: transcript_batch([normalized_turn(0, "hi")], next_token="p") for s in sessions}
    )
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


def _open_segment(ctx, chunk_id: str, session: str, *, held: bool, bind: bool = True) -> str:
    n = chunk_id.removeprefix("ch_")
    if bind:
        ctx.stores.environments.record_binding(
            chunk_id=chunk_id, environment_id=f"env-{n}", workdir=f"/ws/{n}", bound_at=_NOW, workspace_id=None
        )
    ctx.stores.lease_record.record_lease(
        NewLease(
            lease_id=f"lease_{n}",
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    ctx.stores.liveness.record_spawn(
        f"lease_{n}",
        pid=1,
        process_start_time="1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
        spawned_at=_NOW,
        spawn_cwd="/ws",
    )
    if bind and not held:
        ctx.stores.environments.record_release(chunk_id=chunk_id, environment_id=f"env-{n}", released_at=_NOW)
    return next(
        s.segment_id for s in ctx.stores.transcript_ledger.open_transcript_segments() if s.lease_id == f"lease_{n}"
    )


def _binding_statements(store: SqlAlchemyRunnerStore, fn) -> int:
    with capture_statements(store.engine) as statements:
        fn()
    return sum(1 for statement, _ in statements if "env_bindings" in statement)


def _world(n: int, *, held: bool):
    sessions = [f"sess-{i}" for i in range(n)]
    ctx, source, store = _ctx(sessions)
    segment_ids = [_open_segment(ctx, f"ch_{i}", sessions[i], held=held) for i in range(n)]
    return ctx, source, store, segment_ids


@pytest.mark.parametrize("held", [True, False])
def test_run_binding_statements_are_flat_as_segments_grow(held: bool) -> None:
    measured = []
    for n in (1, 4):
        ctx, _source, store, _ids = _world(n, held=held)
        measured.append(_binding_statements(store, lambda ctx=ctx: TranscriptPump(ctx).run()))
    assert measured[0] == measured[1]


@pytest.mark.parametrize("held", [True, False])
def test_pump_lease_binding_statements_are_flat_as_other_open_segments_grow(held: bool) -> None:
    measured = []
    for n in (1, 4):
        ctx, _source, store, _ids = _world(n, held=held)
        measured.append(_binding_statements(store, lambda ctx=ctx: TranscriptPump(ctx).pump_lease("lease_0")))
    assert measured[0] == measured[1]


def test_drain_segment_resolves_bindings_once_across_read_windows() -> None:
    measured = []
    for windows in (1, 4):
        ctx, source, store = _ctx(["sess-0"])
        segment_id = _open_segment(ctx, "ch_0", "sess-0", held=False)
        calls = {"n": 0}

        def windowed(session_id, *, spawn_cwd, since, calls=calls, windows=windows):
            calls["n"] += 1
            token = f"p{calls['n']}"
            if calls["n"] < windows:
                return incomplete_batch([normalized_turn(calls["n"], "more")], next_token=token)
            return transcript_batch([normalized_turn(calls["n"], "end")], next_token=token)

        source.turns_since = windowed
        measured.append(
            _binding_statements(
                store,
                lambda ctx=ctx, segment_id=segment_id: TranscriptPump(ctx).drain_segment(
                    segment_id, deadline=_NOW + timedelta(hours=1)
                ),
            )
        )
        assert calls["n"] == windows
    assert measured[0] == measured[1]


def test_run_rows_read_from_bindings_are_flat_as_unrelated_held_bindings_grow() -> None:
    measured = []
    for unrelated in (0, 6):
        ctx, _source, store = _ctx(["sess-0"])
        _open_segment(ctx, "ch_0", "sess-0", held=True)
        for i in range(unrelated):
            ctx.stores.environments.record_binding(
                chunk_id=f"ch_other{i}", environment_id=f"env-o{i}", workdir=f"/ws/o{i}", bound_at=_NOW
            )
        measured.append(count_rows_read(store.engine, lambda ctx=ctx: TranscriptPump(ctx).run()))
    assert measured[0] == measured[1]


def test_each_chunk_reads_in_the_directory_its_binding_names() -> None:
    ctx, source, _store = _ctx(["sess-held", "sess-released", "sess-unbound"])
    _open_segment(ctx, "ch_1", "sess-held", held=True)
    _open_segment(ctx, "ch_2", "sess-released", held=False)
    _open_segment(ctx, "ch_3", "sess-unbound", held=False, bind=False)
    TranscriptPump(ctx).run()
    cwds = {session: cwd for session, cwd, _ in source.turns_since_calls}
    assert cwds["sess-held"] == "/ws/1"
    assert cwds["sess-released"] != "/ws/2"  # a released chunk never reads in its released workdir
    assert len(cwds) == 3
