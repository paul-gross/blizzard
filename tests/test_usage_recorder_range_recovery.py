"""The envelope-less usage fallback reads only its own generation's boundary-to-tail range
(blizzard#437 Phase 4) — never the whole session, which would re-sum an earlier generation's
already-recorded tokens."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.transcript import TranscriptPosition
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.loop.usage import UsageRecorder
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles
from blizzard.wire.facts import USAGE_RECORDED
from tests.runner_fakes import FakeHarness, FakeTranscriptSource, make_store

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
_SESSION_ID = "range-recovery-session"
_CHUNK_ID = "ch_a"
_NODE_ID = "nd_build"


def _usage_payloads_by_lease(store):  # type: ignore[no-untyped-def]
    return {b.lease_id: json.loads(b.payload) for b in store.pending_outbound() if b.kind == USAGE_RECORDED}


def _seed_lease(store) -> None:  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id="lease_a",
            chunk_id=_CHUNK_ID,
            graph_id="gr_1",
            node_id=_NODE_ID,
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )


def _recorder(store, registry) -> UsageRecorder:  # type: ignore[no-untyped-def]
    return UsageRecorder(
        leases=store,
        usage=store,
        clock=FixedClock(_NOW),
        worker_files=WorkerStdoutFiles("", store),  # "" — no envelope ever survives
        workspace_root="",
        harnesses=registry,
        invocation_boundaries=store,
        transcripts_wired=True,
    )


def test_a_later_generations_recovery_range_excludes_an_earlier_generations_tokens(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Two generations, each opened its own boundary, each with no surviving stdout
    envelope: generation 2's range read must start at ITS OWN boundary — the tail
    generation 1 left — never at the session's beginning, re-summing generation 1's lines."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    source = FakeTranscriptSource(lines_by_session={_SESSION_ID: ["a usage-bearing line"]})
    sample = UsageSample(
        kind="spawn",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )
    handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0", pgid=0)
    harness = FakeHarness(handle=handle, verdict=None, transcript_usage=sample, transcript_source=source)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    recorder = _recorder(store, registry)

    # Generation 1: a fresh session's spawn boundary opens on the beginning sentinel.
    store.record_spawn(
        "lease_a",
        pid=1,
        process_start_time="start-1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID),
        spawned_at=_NOW,
    )
    store.record_boundary_open(
        lease_id="lease_a",
        chunk_id=_CHUNK_ID,
        node_id=_NODE_ID,
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_NOW,
    )
    source._tail_positions[_SESSION_ID] = TranscriptPosition("tail-after-gen-1")
    lease_a = store.active_lease("lease_a")
    assert lease_a is not None
    recorder.record_worker(lease_a, bindings=[])

    # Generation 2: a resume opened its own boundary at generation 1's own tail — the
    # range this generation's recovery must be scoped to.
    store.record_spawn(
        "lease_a",
        pid=2,
        process_start_time="start-2",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID),
        spawned_at=_NOW,
    )
    store.record_boundary_open(
        lease_id="lease_a",
        chunk_id=_CHUNK_ID,
        node_id=_NODE_ID,
        epoch=1,
        generation=2,
        kind="resume",
        start_position="tail-after-gen-1",
        opened_at=_NOW,
    )
    source._tail_positions[_SESSION_ID] = TranscriptPosition("tail-after-gen-2")
    lease_a = store.active_lease("lease_a")
    assert lease_a is not None
    recorder.record_worker(lease_a, bindings=[])

    assert source.read_raw_lines_calls == [
        (_SESSION_ID, None, TranscriptPosition("tail-after-gen-1")),
        (_SESSION_ID, TranscriptPosition("tail-after-gen-1"), TranscriptPosition("tail-after-gen-2")),
    ]
    # Each generation's own boundary-scoped read produced its own recorded fact — two
    # facts, one per generation, never one call re-summing what the other already recorded.
    assert len(store.pending_outbound()) == 2


def test_no_boundary_for_this_generation_records_no_sample_and_reads_no_transcript(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A lease that predates the invocation-boundary write (or a store answering ``None``)
    must not fall through to the old whole-session read — no boundary, no sample, no read."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    store.record_spawn(
        "lease_a",
        pid=1,
        process_start_time="start-1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID),
        spawned_at=_NOW,
    )
    # No `record_boundary_open` call at all — the pre-Phase-2 shape.
    source = FakeTranscriptSource(lines_by_session={_SESSION_ID: ["would-be-whole-session-line"]})
    handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0", pgid=0)
    harness = FakeHarness(handle=handle, verdict=None, transcript_source=source)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    recorder = _recorder(store, registry)

    lease_a = store.active_lease("lease_a")
    assert lease_a is not None
    recorder.record_worker(lease_a, bindings=[])

    assert source.read_raw_lines_calls == []
    assert _usage_payloads_by_lease(store) == {}


def test_a_boundary_with_no_start_position_reads_from_the_beginning(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``start_position is None`` is the fresh-session beginning sentinel, not a failure —
    ``read_raw_lines`` is still called, with ``start=None``."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    store.record_spawn(
        "lease_a",
        pid=1,
        process_start_time="start-1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID),
        spawned_at=_NOW,
    )
    store.record_boundary_open(
        lease_id="lease_a",
        chunk_id=_CHUNK_ID,
        node_id=_NODE_ID,
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_NOW,
    )
    source = FakeTranscriptSource(
        lines_by_session={_SESSION_ID: ["a line"]},
        tail_positions_by_session={_SESSION_ID: TranscriptPosition("tail-1")},
    )
    sample = UsageSample(
        kind="spawn",
        model="m",
        input_tokens=7,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )
    handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0", pgid=0)
    harness = FakeHarness(handle=handle, verdict=None, transcript_usage=sample, transcript_source=source)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    recorder = _recorder(store, registry)

    lease_a = store.active_lease("lease_a")
    assert lease_a is not None
    recorder.record_worker(lease_a, bindings=[])

    assert source.read_raw_lines_calls == [(_SESSION_ID, None, TranscriptPosition("tail-1"))]
    assert _usage_payloads_by_lease(store)["lease_a"]["input_tokens"] == 7


def test_a_nudge_opened_boundary_still_recovers_a_resume_labeled_generation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A generation whose worker-starting boundary was opened under ``kind="nudge"`` still
    recovers, even though `record_worker`'s own usage-kind label for it reads ``"resume"``
    — a usage-accounting label, never a reliable pointer to the boundary that opened it."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    session = SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID)
    # Two spawn rows — `lease_generation` reads 2, so `record_worker`'s own usage-kind
    # label reads "resume" — the boundary that actually started it is "nudge", not "resume".
    store.record_spawn("lease_a", pid=1, process_start_time="start-1", session=session, spawned_at=_NOW)
    store.record_spawn("lease_a", pid=2, process_start_time="start-2", session=session, spawned_at=_NOW)
    store.record_boundary_open(
        lease_id="lease_a",
        chunk_id=_CHUNK_ID,
        node_id=_NODE_ID,
        epoch=1,
        generation=2,
        kind="nudge",
        start_position="tail-after-gen-1",
        opened_at=_NOW,
    )
    source = FakeTranscriptSource(
        lines_by_session={_SESSION_ID: ["a line"]},
        tail_positions_by_session={_SESSION_ID: TranscriptPosition("tail-after-gen-2")},
    )
    sample = UsageSample(
        kind="resume",
        model="m",
        input_tokens=42,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )
    handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0", pgid=0)
    harness = FakeHarness(handle=handle, verdict=None, transcript_usage=sample, transcript_source=source)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    recorder = _recorder(store, registry)

    lease_a = store.active_lease("lease_a")
    assert lease_a is not None
    recorder.record_worker(lease_a, bindings=[])

    assert source.read_raw_lines_calls == [
        (_SESSION_ID, TranscriptPosition("tail-after-gen-1"), TranscriptPosition("tail-after-gen-2"))
    ]
    assert _usage_payloads_by_lease(store)["lease_a"]["input_tokens"] == 42


def test_a_worker_boundary_with_an_unreadable_start_records_no_sample_and_reads_no_transcript(  # type: ignore[no-untyped-def]
    tmp_path,
) -> None:
    """``start_unreadable=True`` must never be treated as "read from zero" — a transient tail
    read failure on a RESUME/JUDGE/NUDGE boundary is never a stand-in for the fresh-session
    sentinel (blizzard#437 F2/F10)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    store.record_spawn(
        "lease_a",
        pid=1,
        process_start_time="start-1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID),
        spawned_at=_NOW,
    )
    store.record_boundary_open(
        lease_id="lease_a",
        chunk_id=_CHUNK_ID,
        node_id=_NODE_ID,
        epoch=1,
        generation=1,
        kind="resume",
        start_position=None,
        start_unreadable=True,
        opened_at=_NOW,
    )
    source = FakeTranscriptSource(lines_by_session={_SESSION_ID: ["would-be-whole-session-line"]})
    handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0", pgid=0)
    harness = FakeHarness(handle=handle, verdict=None, transcript_source=source)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    recorder = _recorder(store, registry)

    lease_a = store.active_lease("lease_a")
    assert lease_a is not None
    recorder.record_worker(lease_a, bindings=[])

    assert source.read_raw_lines_calls == []
    assert _usage_payloads_by_lease(store) == {}


def test_a_readable_judge_boundary_caps_the_worker_sample_at_its_own_start(  # type: ignore[no-untyped-def]
    tmp_path,
) -> None:
    """The companion, positive case: a judge boundary that DID read its own start caps the
    worker's own range read there — its own later turns never bleed into the worker's sum."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    store.record_spawn(
        "lease_a",
        pid=1,
        process_start_time="start-1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID),
        spawned_at=_NOW,
    )
    store.record_boundary_open(
        lease_id="lease_a",
        chunk_id=_CHUNK_ID,
        node_id=_NODE_ID,
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_NOW,
    )
    store.record_boundary_open(
        lease_id="lease_a",
        chunk_id=_CHUNK_ID,
        node_id=_NODE_ID,
        epoch=1,
        generation=1,
        kind="judge",
        start_position="tail-at-judge",
        opened_at=_NOW,
    )
    source = FakeTranscriptSource(
        lines_by_session={_SESSION_ID: ["a line"]},
        tail_positions_by_session={_SESSION_ID: TranscriptPosition("tail-now")},
    )
    sample = UsageSample(
        kind="spawn",
        model="m",
        input_tokens=5,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )
    handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0", pgid=0)
    harness = FakeHarness(handle=handle, verdict=None, transcript_usage=sample, transcript_source=source)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    recorder = _recorder(store, registry)

    lease_a = store.active_lease("lease_a")
    assert lease_a is not None
    recorder.record_worker(lease_a, bindings=[])

    # Capped at the judge's own start — never the raw tail (`tail-now`), which would bleed
    # the judge's own later turns into the worker's own fallback sum.
    assert source.read_raw_lines_calls == [(_SESSION_ID, None, TranscriptPosition("tail-at-judge"))]
    assert _usage_payloads_by_lease(store)["lease_a"]["input_tokens"] == 5


def test_a_judge_boundary_with_an_unreadable_start_skips_the_worker_sample_entirely(  # type: ignore[no-untyped-def]
    tmp_path,
) -> None:
    """A judge boundary exists but its own start could not be read: falling back to "tail
    right now" would risk the judge's own later turns bleeding into the worker's own sum —
    skip the sample instead (blizzard#437 F10)."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    store.record_spawn(
        "lease_a",
        pid=1,
        process_start_time="start-1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID),
        spawned_at=_NOW,
    )
    store.record_boundary_open(
        lease_id="lease_a",
        chunk_id=_CHUNK_ID,
        node_id=_NODE_ID,
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_NOW,
    )
    store.record_boundary_open(
        lease_id="lease_a",
        chunk_id=_CHUNK_ID,
        node_id=_NODE_ID,
        epoch=1,
        generation=1,
        kind="judge",
        start_position=None,
        start_unreadable=True,
        opened_at=_NOW,
    )
    source = FakeTranscriptSource(
        lines_by_session={_SESSION_ID: ["would-be-whole-session-line"]},
        tail_positions_by_session={_SESSION_ID: TranscriptPosition("tail-now")},
    )
    handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0", pgid=0)
    harness = FakeHarness(handle=handle, verdict=None, transcript_source=source)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    recorder = _recorder(store, registry)

    lease_a = store.active_lease("lease_a")
    assert lease_a is not None
    recorder.record_worker(lease_a, bindings=[])

    assert source.read_raw_lines_calls == []
    assert _usage_payloads_by_lease(store) == {}


def test_advance_boundary_moves_the_judge_transcript_range_past_a_stale_signal(  # type: ignore[no-untyped-def]
    tmp_path,
) -> None:
    """blizzard#594: a judge-usage-limit park's resume reuses the SAME ``(lease, generation,
    "judge")`` boundary for its fresh elicitation — ``record_boundary_open``'s check-then-
    insert never mints a second row for one generation's judge phase. Without advancing that
    standing boundary in place, the fresh elicitation's own classification would re-read the
    limited elicitation's own rate-limit signal off the transcript forever."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    store.record_spawn(
        "lease_a",
        pid=1,
        process_start_time="start-1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID),
        spawned_at=_NOW,
    )
    store.record_boundary_open(
        lease_id="lease_a",
        chunk_id=_CHUNK_ID,
        node_id=_NODE_ID,
        epoch=1,
        generation=1,
        kind="judge",
        start_position="tail-at-first-judge",
        opened_at=_NOW,
    )
    source = FakeTranscriptSource(
        lines_by_session={_SESSION_ID: ["a line"]},
        tail_positions_by_session={_SESSION_ID: TranscriptPosition("tail-now")},
    )
    handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0", pgid=0)
    harness = FakeHarness(handle=handle, verdict=None, transcript_source=source)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    recorder = _recorder(store, registry)
    lease_a = store.active_lease("lease_a")
    assert lease_a is not None

    recorder.judge_transcript_lines(lease_a, bindings=[], generation=1)
    assert source.read_raw_lines_calls == [
        (_SESSION_ID, TranscriptPosition("tail-at-first-judge"), TranscriptPosition("tail-now"))
    ]

    # A second row is never minted for the same (lease, generation, "judge") — this call
    # reuses and advances the standing one, past the limited elicitation's own turn.
    store.advance_boundary(
        lease_id="lease_a",
        generation=1,
        kind="judge",
        start_position="tail-after-limited-elicitation",
        opened_at=_NOW,
    )
    advanced = store.boundary("lease_a", 1, "judge")
    assert advanced is not None
    assert advanced.start_position == "tail-after-limited-elicitation"

    recorder.judge_transcript_lines(lease_a, bindings=[], generation=1)
    assert source.read_raw_lines_calls[-1] == (
        _SESSION_ID,
        TranscriptPosition("tail-after-limited-elicitation"),
        TranscriptPosition("tail-now"),
    )
