"""``UsageRecorder`` prices an unpinned generation from the model its own transcript names
(blizzard#629): the observation is asked for only when the lease stamped no model and the
transcripts lane is wired, read once per generation, and handed into both the envelope
parse and the transcript fallback — a pinned lease never pays for the read."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.internal.opencode_adapter import OpenCodeAdapter
from blizzard.runner.harness.internal.opencode_price_cache import OpenCodeModelPrice, OpenCodeRate
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.loop.usage import UsageRecorder
from blizzard.runner.loop.worker_stdout import WorkerStdoutFiles
from blizzard.wire.facts import USAGE_RECORDED
from tests.runner_fakes import FakeHarness, FakeProbe, FakeTranscriptSource, make_store

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)
_SESSION_ID = "ses_a"
_LUNA_STEP_ESTIMATE = (40 * 0.20 + 8 * 1.20) / 1_000_000


def _usage_payloads(store):  # type: ignore[no-untyped-def]
    return [json.loads(b.payload) for b in store.pending_outbound() if b.kind == USAGE_RECORDED]


def _seed_lease(store, *, harness_id: str, resolved_model: str | None, judge_boundary: bool = False) -> None:  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
            resolved_model=resolved_model,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(harness_id, _SESSION_ID),
        spawned_at=_NOW,
    )
    kinds = ["spawn", "judge"] if judge_boundary else ["spawn"]
    for kind in kinds:
        store.record_boundary_open(
            lease_id="lease_1",
            chunk_id="ch_1",
            node_id="nd_build",
            epoch=1,
            generation=1,
            kind=kind,
            start_position=None,
            opened_at=_NOW,
        )


class _LunaPriceCatalog:
    def price_for(self, provider: str, model: str) -> OpenCodeModelPrice | None:
        if (provider, model) != ("openai", "gpt-5.6-luna"):
            return None
        return OpenCodeModelPrice(base=OpenCodeRate(input=0.20, output=1.20, cache_read=0.02, cache_write=0.25))


_TOKENS = {"input": 40, "output": 8, "reasoning": 0, "cache": {"read": 0, "write": 0}}

# The invocation's own stdout: a subscription step, so usage is present but unpriced on its own.
_RUN_EVENT_STDOUT = json.dumps(
    {
        "type": "step_finish",
        "sessionID": _SESSION_ID,
        "part": {
            "id": "prt_1",
            "sessionID": _SESSION_ID,
            "messageID": "msg_1",
            "type": "step-finish",
            "reason": "stop",
            "cost": 0,
            "tokens": _TOKENS,
        },
    }
)

# The same step as the session export names it — the only place OpenCode says which model ran.
_EXPORT_LINE = json.dumps(
    {
        "info": {
            "id": "msg_1",
            "sessionID": _SESSION_ID,
            "role": "assistant",
            "providerID": "openai",
            "modelID": "gpt-5.6-luna",
        },
        "parts": [
            {
                "id": "prt_1",
                "sessionID": _SESSION_ID,
                "messageID": "msg_1",
                "type": "step-finish",
                "reason": "stop",
                "cost": 0,
                "tokens": _TOKENS,
            }
        ],
    }
)


def _opencode_recorder(tmp_path, store, *, transcripts_wired: bool = True):  # type: ignore[no-untyped-def]
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    (stdout_dir / "lease_1.1.stdout").write_text(_RUN_EVENT_STDOUT)
    source = FakeTranscriptSource(lines_by_session={_SESSION_ID: [_EXPORT_LINE]})
    probe = FakeProbe()
    adapter = OpenCodeAdapter(
        worker_env=AllowlistedEnv.of(()),
        process=probe,
        launcher=ProcessLauncher(probe),
        transcript_source=source,
        price_catalog=_LunaPriceCatalog(),
    )
    registry = HarnessRegistry({OPENCODE_HARNESS_ID: HarnessBinding(adapter=adapter, transcript_source=source)})
    recorder = UsageRecorder(
        leases=store,
        usage=store,
        clock=FixedClock(_NOW),
        worker_files=WorkerStdoutFiles(str(stdout_dir), store),
        workspace_root="/ws",
        harnesses=registry,
        invocation_boundaries=store,
        transcripts_wired=transcripts_wired,
    )
    return recorder, source


def test_an_unpinned_opencode_generation_is_priced_from_the_model_its_export_names(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, harness_id=OPENCODE_HARNESS_ID, resolved_model=None)
    recorder, source = _opencode_recorder(tmp_path, store)
    lease = store.active_lease("lease_1")
    assert lease is not None

    recorder.record_worker(lease, bindings=[])

    payloads = _usage_payloads(store)
    assert len(payloads) == 1
    assert payloads[0]["model"] == "openai/gpt-5.6-luna"
    assert payloads[0]["input_tokens"] == 40
    assert payloads[0]["cost_usd"] is None
    assert payloads[0]["estimated_cost_usd"] == pytest.approx(_LUNA_STEP_ESTIMATE)
    # The range is read once for the observation, never again for the fallback the
    # envelope parse made unnecessary.
    assert len(source.read_raw_lines_calls) == 1


def test_a_pinned_generation_reads_no_transcript_for_an_observation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, harness_id=OPENCODE_HARNESS_ID, resolved_model="openai/gpt-5.6-luna")
    recorder, source = _opencode_recorder(tmp_path, store)
    lease = store.active_lease("lease_1")
    assert lease is not None

    recorder.record_worker(lease, bindings=[])

    payloads = _usage_payloads(store)
    assert len(payloads) == 1
    assert payloads[0]["model"] == "openai/gpt-5.6-luna"
    assert payloads[0]["estimated_cost_usd"] == pytest.approx(_LUNA_STEP_ESTIMATE)
    assert source.read_raw_lines_calls == []


def test_an_unpinned_generation_with_the_lane_unwired_is_never_observed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, harness_id=OPENCODE_HARNESS_ID, resolved_model=None)
    recorder, source = _opencode_recorder(tmp_path, store, transcripts_wired=False)
    lease = store.active_lease("lease_1")
    assert lease is not None

    recorder.record_worker(lease, bindings=[])

    payloads = _usage_payloads(store)
    assert len(payloads) == 1
    assert payloads[0]["model"] == "opencode"
    assert payloads[0]["estimated_cost_usd"] is None
    assert source.read_raw_lines_calls == []


def _fake_recorder(tmp_path, store, harness: FakeHarness, source: FakeTranscriptSource, *, transcripts_wired: bool):  # type: ignore[no-untyped-def]
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    (stdout_dir / "lease_1.1.stdout").write_text("<envelope>")
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    return UsageRecorder(
        leases=store,
        usage=store,
        clock=FixedClock(_NOW),
        worker_files=WorkerStdoutFiles(str(stdout_dir), store),
        workspace_root="/ws",
        harnesses=registry,
        invocation_boundaries=store,
        transcripts_wired=transcripts_wired,
    )


def _sample(kind: str) -> UsageSample:
    return UsageSample(
        kind=kind,  # type: ignore[arg-type]
        model="fake-model",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )


def test_the_judge_sample_is_attributed_to_the_model_its_own_range_names(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`record_attempt` follows the worker's rule over the judge's own boundary-to-tail
    range: the observation rides into the judge's `parse_usage` as `model`."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, harness_id=CLAUDE_CODE_HARNESS_ID, resolved_model=None, judge_boundary=True)
    handle = WorkerHandle(session_id=_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    source = FakeTranscriptSource(lines_by_session={_SESSION_ID: ["a transcript line"]})
    harness = FakeHarness(
        handle=handle,
        verdict="pass",
        usage_by_kind={"spawn": _sample("spawn"), "judge": _sample("judge")},
        transcript_source=source,
        observed_model="claude-observed",
    )
    recorder = _fake_recorder(tmp_path, store, harness, source, transcripts_wired=True)
    lease = store.active_lease("lease_1")
    assert lease is not None

    recorder.record_attempt(lease, bindings=[], judge_output="<judged>")

    # One observation per invocation — the worker's range, then the judge's — each handed
    # into its own `parse_usage` in place of the absent stamp.
    assert harness.usage_models == ["claude-observed", "claude-observed"]
    assert len(harness.observed_model_calls) == 2
    assert len(_usage_payloads(store)) == 2


def test_a_pinned_lease_hands_its_stamp_to_both_samples_without_observing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, harness_id=CLAUDE_CODE_HARNESS_ID, resolved_model="claude-pinned", judge_boundary=True)
    handle = WorkerHandle(session_id=_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    source = FakeTranscriptSource(lines_by_session={_SESSION_ID: ["a transcript line"]})
    harness = FakeHarness(
        handle=handle,
        verdict="pass",
        usage_by_kind={"spawn": _sample("spawn"), "judge": _sample("judge")},
        transcript_source=source,
        observed_model="claude-observed",
    )
    recorder = _fake_recorder(tmp_path, store, harness, source, transcripts_wired=True)
    lease = store.active_lease("lease_1")
    assert lease is not None

    recorder.record_attempt(lease, bindings=[], judge_output="<judged>")

    assert harness.usage_models == ["claude-pinned", "claude-pinned"]
    assert harness.observed_model_calls == []
    assert source.read_raw_lines_calls == []
