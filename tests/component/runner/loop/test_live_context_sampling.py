"""Pass snapshots drive the real running-session context sampling lane."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.composition import build_host_process
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.context_sample import ContextSampleSweep
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    make_context,
    make_store,
)
from tests.support.runner_mint import new_mint


def test_live_warning_and_cadence_apply_only_to_next_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(
        tmp_path, top=f'db_url = "{url}"', runner="[runner.context]\nwarn_tokens = 500000\nsample_interval_seconds = 60"
    )
    config = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    now = datetime(2026, 8, 11, 9, tzinfo=UTC)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, now)
    )
    ready, halted = seed_runners(config, list(config.runners))
    assert not halted
    host = build_host_process(config, ready)
    runner = host.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    settings = runner.stores.settings
    store.record_mint(
        new_mint(
            NewLease(
                lease_id="lease_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                session_name="code",
                resolved_model="sonnet",
                resolved_effort="medium",
                created_at=now,
            )
        )
    )
    store.record_spawn(
        "lease_1",
        pid=1,
        process_start_time="t",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-live"),
        spawned_at=now,
    )
    clock = FixedClock(now)
    tokens = {"sess-live": 200000}
    source = FakeTranscriptSource(context_tokens_by_session=tokens)
    fake = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(
            handle=WorkerHandle(session_id="sess-live", pid=1, process_start_time="t", pgid=1),
            verdict="pass",
            transcript_source=source,
        ),
        probe=FakeProbe(),
        clock=clock,
        registered=False,
    )
    calls: list[int] = []

    def run(ctx: LoopContext) -> None:
        current = replace(fake, config=ctx.config)
        ContextSampleSweep(current).run()
        calls.append(len(source.context_tokens_calls))
        if len(calls) == 1:
            settings.write(
                Change("edit", sets={"context.warn_tokens": 300000, "context.sample_interval_seconds": 10}),
                if_match=settings.snapshot().revision,
                validate=reader.validate,
                door="api",
                actor="test",
            )
            tokens["sess-live"] = 400000
            clock.advance(timedelta(seconds=20))
            ContextSampleSweep(current).run()
            calls.append(len(source.context_tokens_calls))

    monkeypatch.setattr(loop_wiring, "tick", run)
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        warnings = [json.loads(f.payload) for f in store.pending_outbound() if f.kind == EVENT_RECORDED]
        assert calls == [1, 1, 2]
        assert len(warnings) == 1
        assert warnings[0]["detail"]["warn_tokens"] == 300000
        assert warnings[0]["detail"]["context_tokens"] == 400000
    finally:
        stepper.close()
        host.close()


def test_live_chunk_cap_parks_at_completion_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from blizzard.foundation.chunk_status import ChunkStatus
    from blizzard.foundation.fact_kinds import ESCALATION_RECORDED
    from blizzard.foundation.node_steps import ApplyOutcome
    from blizzard.runner.lifecycle.drain import OutboundDrain
    from blizzard.runner.node_steps.chunk_state import ChunkSpend, ChunkState
    from blizzard.runner.node_steps.submissions import ApplyReply

    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    config = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    now = datetime(2026, 8, 11, 9, tzinfo=UTC)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, now)
    )
    ready, halted = seed_runners(config, list(config.runners))
    assert not halted
    host = build_host_process(config, ready)
    runner = host.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    settings = runner.stores.settings
    store.record_mint(
        new_mint(
            NewLease(
                lease_id="lease_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                session_name="code",
                resolved_model="sonnet",
                resolved_effort="medium",
                created_at=now,
            )
        )
    )
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkState(
        chunk_id="ch_1",
        status=ChunkStatus.RUNNING,
        latest_epoch=1,
        route_runner_id="r1",
        cost=ChunkSpend(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=7,
            cost_partial=False,
            estimated_cost_usd=None,
        ),
    )
    fake = make_context(
        store,
        hub=hub,
        provider=FakeProvider({}),
        harness=FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict="pass"),
        probe=FakeProbe(),
        registered=False,
    )
    observed: list[bool] = []

    def run(ctx: LoopContext) -> None:
        drain = OutboundDrain(replace(fake, config=ctx.config))
        lease = store.active_lease_for_chunk("ch_1")
        assert lease is not None
        if not observed:
            observed.append(drain._spend_cap_breach(lease) is not None)  # pyright: ignore[reportPrivateUsage]
            settings.write(
                Change("edit", sets={"cost.chunk_cap_usd": 5}),
                if_match=settings.snapshot().revision,
                validate=reader.validate,
                door="api",
                actor="test",
            )
            observed.append(drain._spend_cap_breach(lease) is not None)  # pyright: ignore[reportPrivateUsage]
        else:
            drain._consume(lease, ApplyReply(outcome=ApplyOutcome.NEXT))  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(loop_wiring, "tick", run)
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        assert observed == [False, False]
        assert store.active_lease_for_chunk("ch_1") is None
        escalations = [json.loads(f.payload) for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED]
        assert len(escalations) == 1
        assert escalations[0]["cause"] == "spend-cap"
        assert escalations[0]["detail"] == "spend cap $5.00 reached (spend $7.00)"
    finally:
        stepper.close()
        host.close()
