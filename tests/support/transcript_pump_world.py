"""A transcript pump's world for its per-entry-point read tests: a loop context shipping transcripts from a scripted
source, a minted lease, a source read in several windows, and a per-table statement count."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.transcript import TranscriptBatch, TranscriptPosition
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopConfig, LoopContext
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

PUMP_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def one_turn_batches(sessions: list[str]) -> dict[str, TranscriptBatch]:
    """Each session's source holding one complete turn."""
    return {s: transcript_batch([normalized_turn(0, "hi")], next_token="p") for s in sessions}


def pump_context(source: FakeTranscriptSource) -> tuple[LoopContext, SqlAlchemyRunnerStore]:
    """A loop context shipping transcripts, every session read through ``source``, and its store."""
    store = make_store("sqlite://")
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
    return ctx, store


def mint_lease(ctx: LoopContext, lease_id: str, chunk_id: str) -> None:
    """Mint ``lease_id`` on ``chunk_id``, unspawned."""
    ctx.stores.lease_record.record_mint(
        new_mint(
            NewLease(
                lease_id=lease_id,
                chunk_id=chunk_id,
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=PUMP_NOW,
            )
        )
    )


def lease_segment(ctx: LoopContext, lease_id: str) -> str:
    """The open segment of ``lease_id``."""
    return next(s.segment_id for s in ctx.stores.transcript_ledger.open_transcript_segments() if s.lease_id == lease_id)


def read_in_windows(source: FakeTranscriptSource, windows: int) -> dict[str, int]:
    """Script ``source`` to reach its end on its ``windows``-th read; answer the live read count."""
    calls = {"n": 0}

    def windowed(session_id: str, *, spawn_cwd: str | None, since: TranscriptPosition | None) -> TranscriptBatch:
        calls["n"] += 1
        token = f"p{calls['n']}"
        if calls["n"] < windows:
            return incomplete_batch([normalized_turn(calls["n"], "more")], next_token=token)
        return transcript_batch([normalized_turn(calls["n"], "end")], next_token=token)

    source.turns_since = windowed
    return calls


def statements_touching(store: SqlAlchemyRunnerStore, table: str, fn: Callable[[], object]) -> int:
    """How many statements ``fn`` issues that name ``table``."""
    with capture_statements(store.engine) as statements:
        fn()
    return sum(1 for statement, _ in statements if table in statement)
