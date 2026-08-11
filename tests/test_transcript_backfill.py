"""The transcript backfill verb (component tier, blizzard#250) — store-driven session
selection, dedupe by session id, the merged import of a resumed session, and the
imported/already-present/gone report."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.transcript import NormalizedTurn, TranscriptBatch, TranscriptPosition
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.transcript_backfill import TranscriptBackfill, TranscriptReshipError
from blizzard.runner.loop.transcript_pump import CHUNK_TRANSCRIPT_MAX_BYTES, MAX_BUFFERED_BYTES
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    StubbedBufferBytesStore,
    make_context,
    make_store,
    runner_invariant_violations,
    strip_transcript_segments,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)


def _turn(index: int, text: str) -> NormalizedTurn:
    return NormalizedTurn(
        index=index,
        kind="asst",
        timestamp=_NOW,
        text=text,
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


def _batch(session_id: str, turns: list[NormalizedTurn]) -> TranscriptBatch:
    return TranscriptBatch(
        session_id=session_id,
        available=True,
        reason=None,
        turns=turns,
        unlinked_sidechains=[],
        next_position=TranscriptPosition(f"{session_id}-eof"),
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version="fake/1",
        harness_version="claude/9",
    )


def _ctx(*, sessions: dict[str, list[NormalizedTurn]], on_disk: set[str] | None = None):  # type: ignore[no-untyped-def]
    """A context whose harness source holds ``sessions``; ``on_disk`` narrows which of them
    still have a file, defaulting to all of them."""
    present = sessions.keys() if on_disk is None else on_disk
    source = FakeTranscriptSource(
        batches_by_session={s: _batch(s, turns) for s, turns in sessions.items() if s in present},
        sizes_by_session=dict.fromkeys(present, 1024),
    )
    store = make_store("sqlite://")
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(
            handle=WorkerHandle(session_id="sess-a", pid=1, process_start_time="1"),
            verdict=None,
            transcript_source=source,
        ),
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", transcripts_ship=True),
    )
    return ctx, source


def _historical_lease(ctx, *, lease_id: str, session_id: str, epoch: int, node_id: str = "nd_build", at=_NOW) -> None:  # type: ignore[no-untyped-def]
    """A closed lease that ran ``session_id`` and left no segment — the pre-lane shape."""
    ctx.store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id=node_id,
            node_name=node_id.removeprefix("nd_"),
            epoch=epoch,
            runner_id="r1",
            retries_max=2,
            created_at=at,
        )
    )
    ctx.store.record_spawn(lease_id, pid=1, process_start_time="1", session_id=session_id, spawned_at=at)
    ctx.store.record_closure(lease_id=lease_id, chunk_id="ch_1", node_id=node_id, reason="transitioned", closed_at=at)


def _shipped_bodies(ctx) -> list[dict]:  # type: ignore[no-untyped-def]
    hub = ctx.hub
    assert isinstance(hub, FakeHub)
    return [json.loads(record.model_dump_json()) for record in hub.transcripts_pushed]


def test_it_imports_a_historical_session_as_one_finalized_segment() -> None:
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello"), _turn(1, "world")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)

    report = TranscriptBackfill(ctx).run()

    assert (report.imported, report.already_present, report.gone) == (1, 0, 0)
    assert ctx.store.open_transcript_segments() == []  # finalized, not left open
    bodies = _shipped_bodies(ctx)
    content = [b for b in bodies if not b["final"]]
    assert len(content) == 1
    assert [t["text"] for t in content[0]["turns"]] == ["hello", "world"]
    assert (content[0]["chunk_id"], content[0]["node_id"], content[0]["epoch"]) == ("ch_1", "nd_build", 1)
    assert runner_invariant_violations(ctx.store) == []


def test_a_backfilled_segment_ships_a_version_stamped_final_marker() -> None:
    """AC5 — an ordinary segment: the same closing marker and version stamp the live lane
    ships, so both UIs read it with no backfill-specific case."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)

    TranscriptBackfill(ctx).run()

    final = [b for b in _shipped_bodies(ctx) if b["final"]]
    assert len(final) == 1
    assert final[0]["normalizer_version"] == "fake/1"
    assert final[0]["harness_version"] == "claude/9"
    assert final[0]["record_truncated"] is False


def test_it_reads_only_the_sessions_its_own_store_names() -> None:
    """AC1 — the harness directory also holds the operator's own sessions; nothing here may
    open one, so neither the content read nor the on-disk probe ever names it."""
    ctx, source = _ctx(sessions={"sess-a": [_turn(0, "fleet")], "sess-operator": [_turn(0, "personal")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)

    TranscriptBackfill(ctx).run()

    assert [call[0] for call in source.turns_since_calls] == ["sess-a"]
    assert source.size_bytes_calls == ["sess-a"]


def test_a_second_run_imports_nothing_twice() -> None:
    """AC2 — the dedupe key is the session, not the lease: the second run must skip the
    resuming lease too, which holds no segment of its own."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    _historical_lease(ctx, lease_id="lease_2", session_id="sess-a", epoch=2, at=_NOW + timedelta(hours=1))
    strip_transcript_segments(ctx.store)

    TranscriptBackfill(ctx).run()
    shipped_after_first = len(_shipped_bodies(ctx))
    second = TranscriptBackfill(ctx).run()

    assert (second.imported, second.already_present, second.gone) == (0, 1, 0)
    assert len(_shipped_bodies(ctx)) == shipped_after_first


def test_a_session_the_live_lane_already_segmented_under_a_later_lease_is_left_alone() -> None:
    """A session straddling the lane's arrival: its first lease predates segments entirely,
    and only a session-wide check sees that the hub already holds the very same file."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)
    _historical_lease(ctx, lease_id="lease_2", session_id="sess-a", epoch=2, at=_NOW + timedelta(hours=1))

    report = TranscriptBackfill(ctx).run()

    assert (report.imported, report.already_present) == (0, 1)
    assert _shipped_bodies(ctx) == []


def test_a_session_resumed_across_leases_imports_once_at_its_first_lease() -> None:
    """AC3 — a pre-epic in-place resume recorded no offset, so the one merged file imports
    once, attributed to the lease its session began on."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "first"), _turn(1, "after resume")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1, node_id="nd_build")
    _historical_lease(
        ctx, lease_id="lease_2", session_id="sess-a", epoch=2, node_id="nd_review", at=_NOW + timedelta(hours=1)
    )
    strip_transcript_segments(ctx.store)

    report = TranscriptBackfill(ctx).run()

    assert report.imported == 1
    segments = _shipped_bodies(ctx)
    assert len({b["segment_id"] for b in segments}) == 1
    content = [b for b in segments if not b["final"]]
    assert (content[0]["node_id"], content[0]["epoch"], content[0]["spawn_generation"]) == ("nd_build", 1, 1)
    assert [t["text"] for t in content[0]["turns"]] == ["first", "after resume"]


def test_a_session_whose_file_is_gone_is_reported_not_errored() -> None:
    """AC4 — the harness rotated the file away; a stated loss, never an exception, and no
    empty segment minted for it."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "kept")], "sess-b": [_turn(0, "rotated")]}, on_disk={"sess-a"})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    _historical_lease(ctx, lease_id="lease_2", session_id="sess-b", epoch=2, at=_NOW + timedelta(hours=1))
    strip_transcript_segments(ctx.store)

    report = TranscriptBackfill(ctx).run()

    assert (report.imported, report.already_present, report.gone) == (1, 0, 1)
    bodies = _shipped_bodies(ctx)
    # One segment only: the gone session mints none at all, not an empty one whose final
    # marker would claim a step the hub holds no conversation for.
    assert len({b["segment_id"] for b in bodies}) == 1
    assert {b["epoch"] for b in bodies} == {1}


def test_a_dry_run_classifies_without_writing_or_shipping() -> None:
    ctx, source = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)

    report = TranscriptBackfill(ctx).run(dry_run=True)

    assert report.imported == 1
    assert _shipped_bodies(ctx) == []
    assert ctx.store.pending_transcript_outbound() == []
    assert source.turns_since_calls == []  # only the on-disk probe ran


def test_an_interrupted_run_finishes_its_own_unfinalized_segment() -> None:
    """A crash between opening a segment and finalizing it leaves the session looking
    imported; the rerun drains and closes that segment rather than counting it present."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)
    ctx.store.open_transcript_segment(
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        lease_id="lease_1",
        session_id="sess-a",
        stamped_at=_NOW,
    )

    report = TranscriptBackfill(ctx).run()

    assert (report.imported, report.already_present) == (1, 0)
    assert ctx.store.open_transcript_segments() == []
    assert [b["final"] for b in _shipped_bodies(ctx)] == [False, True]


def test_a_live_leases_open_segment_is_left_to_the_tick() -> None:
    """The pump owns a running worker's segment; a backfill that finalized one would close
    a conversation still being written."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "in flight")]})
    ctx.store.record_lease(
        NewLease(
            lease_id="lease_live",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    ctx.store.record_spawn("lease_live", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)

    report = TranscriptBackfill(ctx).run()

    assert (report.imported, report.already_present) == (0, 1)
    assert len(ctx.store.open_transcript_segments()) == 1


def test_it_defers_the_tail_once_the_outbound_buffer_is_full() -> None:
    """The between-sessions pre-check: with the buffer already over cap on entry, nothing
    is opened at all."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)
    backpressured = TranscriptBackfill(replace(ctx, store=StubbedBufferBytesStore(ctx.store, MAX_BUFFERED_BYTES)))

    report = backpressured.run()

    assert (report.imported, report.deferred) == (0, 1)
    assert _shipped_bodies(ctx) == []


def test_a_buffer_that_fills_mid_drain_defers_the_session_instead_of_sealing_it() -> None:
    """The boundary the pre-check cannot see: the buffer crosses the cap while THIS session
    is draining. Finalizing there would seal a session whose turns were never read, and the
    rerun would call it already-present forever."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)
    # Under cap for the between-sessions pre-check, over it by the pump's own read.
    store = StubbedBufferBytesStore(ctx.store, 0, MAX_BUFFERED_BYTES)

    report = TranscriptBackfill(replace(ctx, store=store)).run()

    assert (report.imported, report.deferred) == (0, 1)
    assert len(ctx.store.open_transcript_segments()) == 1  # left open for the rerun to resume
    assert _shipped_bodies(ctx) == []  # nothing sealed: no final marker claiming an empty step


def test_a_session_unreadable_after_the_on_disk_probe_is_deferred_not_sealed() -> None:
    """The file passed the probe and then failed to read. Closing the segment out there
    reports an import that shipped nothing and locks the session out of every later run."""
    unreadable = TranscriptBatch(
        session_id="sess-a",
        available=False,
        reason="unreadable",
        turns=[],
        unlinked_sidechains=[],
        next_position=None,
        complete=True,
        truncated=False,
        sidechain_truncated=False,
        normalizer_version="fake/1",
        harness_version=None,
    )
    ctx, source = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    source._batches["sess-a"] = unreadable
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)

    first = TranscriptBackfill(ctx).run()

    assert (first.imported, first.deferred, first.gone) == (0, 1, 0)
    assert _shipped_bodies(ctx) == []
    assert len(ctx.store.open_transcript_segments()) == 1

    # The file comes back; the rerun resumes that same segment rather than reporting it present.
    source._batches["sess-a"] = _batch("sess-a", [_turn(0, "hello")])
    second = TranscriptBackfill(ctx).run()

    assert (second.imported, second.already_present) == (1, 0)
    assert [t["text"] for b in _shipped_bodies(ctx) if not b["final"] for t in b["turns"]] == ["hello"]
    assert ctx.store.open_transcript_segments() == []


def test_a_limit_bounds_one_run_and_defers_the_rest() -> None:
    """The operator's bound on a bulk import — the hub's per-runner daily rate is real, and
    a run that blows through it loses the overflow to cap rejections it cannot retry."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "a")], "sess-b": [_turn(0, "b")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    _historical_lease(ctx, lease_id="lease_2", session_id="sess-b", epoch=2, at=_NOW + timedelta(hours=1))
    strip_transcript_segments(ctx.store)

    report = TranscriptBackfill(ctx).run(limit=1)

    assert (report.imported, report.deferred) == (1, 1)
    assert len({b["segment_id"] for b in _shipped_bodies(ctx)}) == 1
    assert TranscriptBackfill(ctx).run(limit=1).imported == 1  # the rerun takes the next one


def test_a_hub_capped_import_is_reported_apart_from_a_whole_one() -> None:
    """`imported` counts what was read and enqueued; the hub refusing it is a different
    fact, and a report that folded the two would call a discarded session imported."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)
    hub = ctx.hub
    assert isinstance(hub, FakeHub)
    hub.reject_transcript_seqs = {1, 2}

    report = TranscriptBackfill(ctx).run()

    assert (report.imported, report.capped) == (1, 1)


def test_the_ship_switch_is_held_in_the_domain_not_only_at_the_cli() -> None:
    """`LoopWiring.backfill_transcripts` is a public entry; the CLI refusal is the operator's
    message, never the enforcement — the same gate the pump holds."""
    ctx, source = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    _historical_lease(ctx, lease_id="lease_1", session_id="sess-a", epoch=1)
    strip_transcript_segments(ctx.store)
    off = replace(ctx, config=replace(ctx.config, transcripts_ship=False))

    report = TranscriptBackfill(off).run()

    assert (report.imported, report.already_present, report.gone) == (0, 0, 0)
    assert source.size_bytes_calls == []


# --- the operator re-ship (supersede an already-imported segment) ---------------


def _import_one(ctx, *, session_id: str = "sess-a") -> str:  # type: ignore[no-untyped-def]
    """Backfill a single historical session and return the segment id it landed under."""
    _historical_lease(ctx, lease_id="lease_1", session_id=session_id, epoch=1)
    strip_transcript_segments(ctx.store)
    TranscriptBackfill(ctx).run()
    content = [b for b in _shipped_bodies(ctx) if not b["final"]]
    assert len(content) == 1
    return str(content[0]["segment_id"])


def test_reship_sends_the_session_again_under_a_new_segment_id() -> None:
    """The hub never overwrites a record it already accepted, so superseding one means a
    SECOND segment carrying the same lease's content — the duplicate is the mechanism."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello"), _turn(1, "world")]})
    first = _import_one(ctx)

    report = TranscriptBackfill(ctx).reship(first)

    assert report.segment_id != first
    assert (report.source_segment_id, report.session_id, report.complete) == (first, "sess-a", True)
    assert (report.turns, report.truncated_reason) == (2, None)
    content = [b for b in _shipped_bodies(ctx) if not b["final"]]
    assert len(content) == 2
    # Same lease coordinates, so the board files both under the one node/epoch.
    assert {(b["chunk_id"], b["node_id"], b["epoch"]) for b in content} == {("ch_1", "nd_build", 1)}
    assert [t["text"] for t in content[1]["turns"]] == ["hello", "world"]
    assert runner_invariant_violations(ctx.store) == []


def test_reship_leaves_the_original_segment_exactly_as_it_shipped() -> None:
    """The record of what the hub was once told stays honest — a re-ship adds, never edits."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    first = _import_one(ctx)
    before = ctx.store.transcript_segment(first)

    TranscriptBackfill(ctx).reship(first)

    assert ctx.store.transcript_segment(first) == before


def test_reship_closes_its_new_segment_out_and_ships_a_final_marker() -> None:
    """A superseding segment is an ordinary one — left open, the board would render it as
    a lease still streaming, and a later backfill would try to resume it."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    first = _import_one(ctx)

    report = TranscriptBackfill(ctx).reship(first)

    assert ctx.store.open_transcript_segments() == []
    final = [b for b in _shipped_bodies(ctx) if b["final"]]
    assert {b["segment_id"] for b in final} == {first, report.segment_id}


def test_reship_refuses_an_unknown_segment_id() -> None:
    """A typo must not silently open a segment against nothing."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    _import_one(ctx)

    with pytest.raises(TranscriptReshipError, match="no such transcript segment"):
        TranscriptBackfill(ctx).reship("seg_nope")

    assert len(_shipped_bodies(ctx)) == 2  # the original import's content + final marker only


def test_reship_refuses_a_session_no_longer_readable() -> None:
    """Nothing is written on this path, so a rerun retries once the transcripts root is
    right — an opened-then-empty segment would instead supersede real content with none."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    first = _import_one(ctx)
    # The batch is still scriptable; only the on-disk probe is gone — exactly the rotated-away
    # shape, where a bare `turns_since` would still answer and quietly ship an empty segment.
    rotated = replace(
        ctx,
        transcripts=FakeTranscriptSource(
            batches_by_session={"sess-a": _batch("sess-a", [_turn(0, "hello")])}, sizes_by_session={}
        ),
    )

    with pytest.raises(TranscriptReshipError, match="not readable by this runner"):
        TranscriptBackfill(rotated).reship(first)

    assert ctx.store.open_transcript_segments() == []


def test_the_reship_ship_switch_is_held_in_the_domain_not_only_at_the_cli() -> None:
    """`LoopWiring.reship_transcript` is a public entry — the same gate the backfill holds."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    first = _import_one(ctx)
    off = replace(ctx, config=replace(ctx.config, transcripts_ship=False))

    with pytest.raises(TranscriptReshipError, match="ship is false"):
        TranscriptBackfill(off).reship(first)


def test_reship_reports_a_chunk_budget_stop_rather_than_a_clean_zero_byte_run() -> None:
    """The pump treats a shipping-stopped segment as caught up, so this path returns
    `complete=True` with zero counts — indistinguishable from a whole re-ship unless the
    stop reason is carried, and re-shipping spends the per-chunk budget a second time."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    first = _import_one(ctx)
    ctx.store.record_transcript_deltas(
        segment_id=first,
        chunk_id="ch_1",
        cursor="spent",
        shipped_bytes=CHUNK_TRANSCRIPT_MAX_BYTES,
        shipped_turns=1,
        normalizer_version="fake/1",
        harness_version="claude/9",
        payloads=[],
        created_at=_NOW,
    )

    report = TranscriptBackfill(ctx).reship(first)

    assert report.shipping_stopped_reason == "chunk_budget_exceeded"
    assert (report.turns, report.shipped_bytes) == (0, 0)


def test_reship_resumes_its_own_unfinished_segment_instead_of_stranding_it() -> None:
    """Rerunning is what the incomplete-read warning tells the operator to do. Opening a
    fresh segment each time would strand the last one open forever, and the board renders an
    open segment as a lease still streaming."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    first = _import_one(ctx)
    stalled = replace(ctx, transcripts=FakeTranscriptSource(sizes_by_session={"sess-a": 1024}))

    incomplete = TranscriptBackfill(stalled).reship(first)  # source unreadable mid-drain -> stays open
    assert not incomplete.complete
    second = TranscriptBackfill(ctx).reship(first)

    assert second.segment_id == incomplete.segment_id  # resumed, not a third segment
    assert second.complete
    assert ctx.store.open_transcript_segments() == []


def test_reship_refuses_a_segment_whose_lease_is_still_active() -> None:
    """`run`'s own rule: a live lease's segment belongs to the tick's pump. Re-shipping one
    races it, leaving two segments reading the same session from different offsets."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    first = _import_one(ctx)
    ctx.store.record_lease(
        NewLease(
            lease_id="lease_live",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=9,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    ctx.store.record_spawn("lease_live", pid=2, process_start_time="2", session_id="sess-live", spawned_at=_NOW)
    live = next(s for s in ctx.store.open_transcript_segments() if s.lease_id == "lease_live")

    with pytest.raises(TranscriptReshipError, match="still active"):
        TranscriptBackfill(ctx).reship(live.segment_id)

    assert first  # the closed-lease segment above is untouched by the refusal


def test_reship_points_its_new_segment_at_the_one_it_supersedes() -> None:
    """The hub's lease read is keyed on the lease, not the segment, so the pointer is what
    keeps a re-ship from rendering the conversation twice. Every record carries it — the
    final marker too, since a segment whose content was capped ships only that."""
    ctx, _ = _ctx(sessions={"sess-a": [_turn(0, "hello")]})
    first = _import_one(ctx)

    report = TranscriptBackfill(ctx).reship(first)

    bodies = [b for b in _shipped_bodies(ctx) if b["segment_id"] == report.segment_id]
    assert bodies and all(b["supersedes"] == first for b in bodies)
    assert any(b["final"] for b in bodies)  # including the closing marker
    # The original's own records claim to supersede nothing, or the hub drops them both.
    assert all(b["supersedes"] is None for b in _shipped_bodies(ctx) if b["segment_id"] == first)
