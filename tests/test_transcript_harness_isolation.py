"""Two harnesses sharing one raw session-id text never collide in a transcript cursor —
the pump, the drain, and the backfill all key a segment by its full
``SessionReference`` (harness + raw id), never the raw id alone. Each scenario below binds
the SAME raw session id to two different harnesses, each with its own fake source, and
proves neither segment ever reads or ships the other's content."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.transcript import NormalizedTurn, TranscriptBatch, TranscriptPosition
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.transcript_backfill import TranscriptBackfill
from blizzard.runner.loop.transcript_drain import TranscriptDrain
from blizzard.runner.loop.transcript_pump import TranscriptPump
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    make_context,
    make_store,
    strip_transcript_segments,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
_SHARED_SESSION_ID = "shared-raw-session"
_OTHER_HARNESS_ID = "other_harness"


def _turn(text: str) -> NormalizedTurn:
    return NormalizedTurn(
        index=0,
        kind="asst",
        timestamp=_NOW,
        text=text,
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


def _batch(text: str, *, cursor: str) -> TranscriptBatch:
    return TranscriptBatch(
        session_id=_SHARED_SESSION_ID,
        available=True,
        reason=None,
        turns=[_turn(text)],
        unlinked_sidechains=[],
        next_position=TranscriptPosition(cursor),
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version="fake/1",
        harness_version="fake/9",
    )


def _two_harness_ctx():  # type: ignore[no-untyped-def]
    """A context bound to two harnesses — ``claude_code`` and ``other_harness`` — each
    with its own fake transcript source, both scripted to answer the SAME raw session id
    with different content."""
    store = make_store("sqlite://")
    source_a = FakeTranscriptSource(
        batches_by_session={_SHARED_SESSION_ID: _batch("hello from claude_code", cursor="pos-a")},
        sizes_by_session={_SHARED_SESSION_ID: 1024},
    )
    source_b = FakeTranscriptSource(
        batches_by_session={_SHARED_SESSION_ID: _batch("hello from other_harness", cursor="pos-b")},
        sizes_by_session={_SHARED_SESSION_ID: 1024},
    )
    # Never spawned through either fake — sessions are seeded directly below — so the
    # handle is a placeholder no assertion here reads.
    unused_handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0")
    harness_a = FakeHarness(handle=unused_handle, verdict=None, transcript_source=source_a)
    harness_b = FakeHarness(handle=unused_handle, verdict=None, transcript_source=source_b)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e_a": "/ws/e_a", "e_b": "/ws/e_b"}),
        harness=harness_a,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
    registry = HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness_a, transcript_source=source_a),
            _OTHER_HARNESS_ID: HarnessBinding(adapter=harness_b, transcript_source=source_b),
        }
    )
    return replace(ctx, harnesses=registry, transcripts_wired=True), source_a, source_b


def _seed_open_segment(ctx, *, chunk_id: str, lease_id: str, env_id: str, harness_id: str) -> None:  # type: ignore[no-untyped-def]
    ctx.stores.environments.record_binding(
        chunk_id=chunk_id, environment_id=env_id, workdir=f"/ws/{env_id}", bound_at=_NOW
    )
    ctx.stores.lease_record.record_lease(
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
    ctx.stores.liveness.record_spawn(
        lease_id,
        pid=1,
        process_start_time="1",
        session=SessionReference(harness_id, _SHARED_SESSION_ID),
        spawned_at=_NOW,
    )


def test_pump_and_drain_never_cross_merge_same_raw_session_id_across_harnesses() -> None:  # type: ignore[no-untyped-def]
    ctx, _, _ = _two_harness_ctx()
    _seed_open_segment(ctx, chunk_id="ch_a", lease_id="lease_a", env_id="e_a", harness_id=CLAUDE_CODE_HARNESS_ID)
    _seed_open_segment(ctx, chunk_id="ch_b", lease_id="lease_b", env_id="e_b", harness_id=_OTHER_HARNESS_ID)

    TranscriptPump(ctx).run()

    segment_a = ctx.stores.transcript_ledger.transcript_segments_for_chunk("ch_a")[0]
    segment_b = ctx.stores.transcript_ledger.transcript_segments_for_chunk("ch_b")[0]
    assert segment_a.session == SessionReference(CLAUDE_CODE_HARNESS_ID, _SHARED_SESSION_ID)
    assert segment_b.session == SessionReference(_OTHER_HARNESS_ID, _SHARED_SESSION_ID)
    # Each segment's own cursor advanced from its OWN source — never the sibling's.
    assert segment_a.cursor == "pos-a"
    assert segment_b.cursor == "pos-b"

    TranscriptDrain(ctx).run()

    hub = ctx.hub
    assert isinstance(hub, FakeHub)
    by_chunk = {record.chunk_id: record for record in hub.transcripts_pushed if not record.final}
    assert [t.text for t in by_chunk["ch_a"].turns] == ["hello from claude_code"]
    assert [t.text for t in by_chunk["ch_b"].turns] == ["hello from other_harness"]


def test_backfill_imports_both_owners_of_the_same_raw_session_id() -> None:  # type: ignore[no-untyped-def]
    """The backfill's dedupe key is the full session (harness + raw id):
    a pre-lane lease under each harness, sharing one raw session id, both import as
    separate segments — neither reads as already-present because of the other."""
    ctx, _, _ = _two_harness_ctx()
    ctx.stores.environments.record_binding(chunk_id="ch_a", environment_id="e_a", workdir="/ws/e_a", bound_at=_NOW)
    ctx.stores.environments.record_binding(chunk_id="ch_b", environment_id="e_b", workdir="/ws/e_b", bound_at=_NOW)
    for chunk_id, lease_id, harness_id in (
        ("ch_a", "lease_a", CLAUDE_CODE_HARNESS_ID),
        ("ch_b", "lease_b", _OTHER_HARNESS_ID),
    ):
        ctx.stores.lease_record.record_lease(
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
        ctx.stores.liveness.record_spawn(
            lease_id,
            pid=1,
            process_start_time="1",
            session=SessionReference(harness_id, _SHARED_SESSION_ID),
            spawned_at=_NOW,
        )
        ctx.stores.lease_record.record_closure(
            lease_id=lease_id, chunk_id=chunk_id, node_id="nd_build", reason="transitioned", closed_at=_NOW
        )
    strip_transcript_segments(ctx.stores.transcript_ledger)  # the pre-lane shape: no segment survives

    report = TranscriptBackfill(ctx).run()

    assert (report.imported, report.already_present, report.gone) == (2, 0, 0)
    hub = ctx.hub
    assert isinstance(hub, FakeHub)
    by_chunk = {record.chunk_id: record for record in hub.transcripts_pushed if not record.final}
    assert [t.text for t in by_chunk["ch_a"].turns] == ["hello from claude_code"]
    assert [t.text for t in by_chunk["ch_b"].turns] == ["hello from other_harness"]
