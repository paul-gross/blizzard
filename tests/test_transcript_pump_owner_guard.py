"""``TranscriptPump``'s per-segment read never raises on an unresolvable session owner, and
never escalates on its own either — a deliberate choice, not an oversight. ``_pump_one`` now
catches ``UnknownHarnessError``/``UnavailableHarnessError`` with a named guard alongside its
callers' blanket per-segment isolation, logged with the owner detail and costing only
``_NOT_ATTEMPTED`` for THIS segment. The lease it belongs to escalates, if at all, through
its own dispatch path — not this read-only side lane."""

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
from blizzard.runner.loop.transcript_pump import TranscriptPump
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    make_context,
    make_store,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_FOREIGN_HARNESS_ID = "foreign"


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


def _batch(session_id: str) -> TranscriptBatch:
    return TranscriptBatch(
        session_id=session_id,
        available=True,
        reason=None,
        turns=[_turn("hi")],
        unlinked_sidechains=[],
        next_position=TranscriptPosition("pos-1"),
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version="fake/1",
        harness_version=None,
    )


def _registry(default_harness, *, unavailable: bool) -> HarnessRegistry:  # type: ignore[no-untyped-def]
    """The default owner alone (an unregistered ``foreign`` id reads as unknown), or —
    when ``unavailable`` — ``foreign`` registered with no transcript source bound (a known
    owner this runner cannot use right now, not an unheard-of one)."""
    bindings = {
        CLAUDE_CODE_HARNESS_ID: HarnessBinding(
            adapter=default_harness, transcript_source=default_harness.transcript_source()
        ),
    }
    if unavailable:
        bindings[_FOREIGN_HARNESS_ID] = HarnessBinding(adapter=default_harness, transcript_source=None)
    return HarnessRegistry(bindings)


def _open_segment(ctx, *, chunk_id: str, lease_id: str, session: SessionReference) -> str:  # type: ignore[no-untyped-def]
    ctx.stores.environments.record_binding(
        chunk_id=chunk_id, environment_id=f"e_{chunk_id}", workdir=f"/ws/{chunk_id}", bound_at=_NOW
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
    ctx.stores.liveness.record_spawn(lease_id, pid=1, process_start_time="1", session=session, spawned_at=_NOW)
    segments = [s for s in ctx.stores.transcript_ledger.open_transcript_segments() if s.lease_id == lease_id]
    return segments[0].segment_id


@pytest.mark.parametrize("unavailable", [False, True], ids=["unknown-owner", "unavailable-owner"])
def test_pump_skips_only_the_segment_with_an_unresolvable_owner(tmp_path, unavailable):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    source = FakeTranscriptSource(batches_by_session={"sess-ok": _batch("sess-ok")})
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-ok", pid=1, process_start_time="1", pgid=1),
        verdict=None,
        transcript_source=source,
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e_ch_blocked": "/ws/ch_blocked", "e_ch_ok": "/ws/ch_ok"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
    ctx = replace(ctx, harnesses=_registry(harness, unavailable=unavailable))

    blocked_segment_id = _open_segment(
        ctx, chunk_id="ch_blocked", lease_id="lease_blocked", session=SessionReference(_FOREIGN_HARNESS_ID, "sess-a")
    )
    ok_segment_id = _open_segment(
        ctx,
        chunk_id="ch_ok",
        lease_id="lease_ok",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-ok"),
    )

    TranscriptPump(ctx).run()  # must not raise

    # The blocked segment's cursor never moved and its lease is untouched — this pump never
    # escalates on its own, that is its owning lease's dispatch path to take.
    blocked = store.transcript_segment(blocked_segment_id)
    assert blocked is not None and blocked.cursor is None
    pending = store.pending_transcript_outbound()
    assert all(delta.segment_id != blocked_segment_id for delta in pending)
    assert store.active_lease("lease_blocked") is not None
    assert store.open_escalations() == []

    # The sibling segment, under a resolvable owner, was still drained this same run().
    ok = store.transcript_segment(ok_segment_id)
    assert ok is not None and ok.cursor == "pos-1"
    assert any(delta.segment_id == ok_segment_id for delta in pending)

    # A repeated run — the same shape a crash-and-replay leaves — skips the same segment
    # the same way every time: no accumulation, no fabricated escalation, no raise.
    TranscriptPump(ctx).run()
    blocked_again = store.transcript_segment(blocked_segment_id)
    assert blocked_again is not None and blocked_again.cursor is None
    assert store.open_escalations() == []
