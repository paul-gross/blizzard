"""Two harnesses sharing one raw session-id text never collide in usage recovery —
the envelope-less fallback sums each generation's own transcript,
read from its recorded owner's own source, never a sibling harness's."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.loop.usage import UsageRecorder
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles
from blizzard.wire.facts import USAGE_RECORDED
from tests.runner_fakes import FakeHarness, FakeTranscriptSource, make_store

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)
_SHARED_SESSION_ID = "shared-usage-session"
_OTHER_HARNESS_ID = "other_harness"


def _usage_payloads_by_lease(store):  # type: ignore[no-untyped-def]
    return {b.lease_id: json.loads(b.payload) for b in store.pending_outbound() if b.kind == USAGE_RECORDED}


def _seed_lease(store, *, lease_id: str, chunk_id: str, harness_id: str, pid: int) -> None:  # type: ignore[no-untyped-def]
    store.record_lease(
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
    store.record_spawn(
        lease_id,
        pid=pid,
        process_start_time=f"start-{pid}",
        session=SessionReference(harness_id, _SHARED_SESSION_ID),
        spawned_at=_NOW,
    )


def test_envelope_less_fallback_sums_each_harnesss_own_transcript_no_cross_read(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """No stdout envelope survives for either generation, so both fall back to summing
    the raw transcript — each must read its OWN recorded owner's source, at the same raw
    session id, without ever summing the sibling harness's lines."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=1)
    _seed_lease(store, lease_id="lease_b", chunk_id="ch_b", harness_id=_OTHER_HARNESS_ID, pid=2)

    sample_a = UsageSample(
        kind="spawn",
        model="claude-model",
        input_tokens=111,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )
    sample_b = UsageSample(
        kind="spawn",
        model="other-model",
        input_tokens=222,
        output_tokens=2,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )
    source_a = FakeTranscriptSource(lines_by_session={_SHARED_SESSION_ID: ["claude_code's own line"]})
    source_b = FakeTranscriptSource(lines_by_session={_SHARED_SESSION_ID: ["other_harness's own line"]})
    # Never spawned through either fake — usage recovery reads only its transcript source
    # — so the handle is a placeholder no assertion here reads.
    unused_handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0")
    harness_a = FakeHarness(handle=unused_handle, verdict=None, transcript_usage=sample_a, transcript_source=source_a)
    harness_b = FakeHarness(handle=unused_handle, verdict=None, transcript_usage=sample_b, transcript_source=source_b)
    registry = HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness_a, transcript_source=source_a),
            _OTHER_HARNESS_ID: HarnessBinding(adapter=harness_b, transcript_source=source_b),
        }
    )
    recorder = UsageRecorder(
        leases=store,
        usage=store,
        clock=FixedClock(_NOW),
        worker_files=WorkerStdoutFiles("", store),  # "" — no envelope ever survives
        workspace_root="",
        harnesses=registry,
        transcripts_wired=True,  # the lane's on/off switch alone; reads still dispatch per-owner
    )

    lease_a = store.active_lease("lease_a")
    lease_b = store.active_lease("lease_b")
    assert lease_a is not None and lease_b is not None
    recorder.record_worker(lease_a, bindings=[])
    recorder.record_worker(lease_b, bindings=[])

    # Each generation's usage was summed by its OWN recorded owner's adapter — never the
    # sibling harness's, despite both sessions sharing the same raw id.
    payloads = _usage_payloads_by_lease(store)
    assert payloads["lease_a"]["input_tokens"] == 111
    assert payloads["lease_a"]["model"] == "claude-model"
    assert payloads["lease_b"]["input_tokens"] == 222
    assert payloads["lease_b"]["model"] == "other-model"


def test_envelope_parse_still_records_with_the_transcripts_lane_off(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``transcripts_wired=False`` disables only the envelope-less fallback (its own
    docstring) — a generation whose own stdout envelope parses fine must still record,
    never silently skipped just because the transcripts lane is off."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=1)
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    (stdout_dir / "lease_a.1.stdout").write_text("<envelope>")
    sample = UsageSample(
        kind="spawn",
        model="claude-model",
        input_tokens=99,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.01,
    )
    unused_handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0")
    harness = FakeHarness(handle=unused_handle, verdict=None, usage_by_kind={"spawn": sample})
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=None)})
    recorder = UsageRecorder(
        leases=store,
        usage=store,
        clock=FixedClock(_NOW),
        worker_files=WorkerStdoutFiles(str(stdout_dir), store),
        workspace_root="",
        harnesses=registry,
        transcripts_wired=False,  # the lane is off entirely — not this generation's own envelope
    )

    lease_a = store.active_lease("lease_a")
    assert lease_a is not None
    recorder.record_worker(lease_a, bindings=[])

    payloads = _usage_payloads_by_lease(store)
    assert payloads["lease_a"]["input_tokens"] == 99
    assert payloads["lease_a"]["model"] == "claude-model"


def test_transcript_fallback_with_no_transcript_source_records_nothing_rather_than_raising(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A recorded owner with an adapter but no bound transcript source (or none registered
    at all) must not raise out of usage recovery — no envelope survived, so the fallback is
    reached, finds nothing it can read, and simply records no sample."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=1)
    unused_handle = WorkerHandle(session_id="unused", pid=0, process_start_time="0")
    harness = FakeHarness(handle=unused_handle, verdict=None)
    # A known owner, adapter-bound, but with no transcript source of its own.
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=None)})
    recorder = UsageRecorder(
        leases=store,
        usage=store,
        clock=FixedClock(_NOW),
        worker_files=WorkerStdoutFiles("", store),  # "" — no envelope ever survives
        workspace_root="",
        harnesses=registry,
        transcripts_wired=True,  # the lane is ON — the fallback is reached
    )

    lease_a = store.active_lease("lease_a")
    assert lease_a is not None
    recorder.record_worker(lease_a, bindings=[])  # must not raise

    assert _usage_payloads_by_lease(store) == {}
