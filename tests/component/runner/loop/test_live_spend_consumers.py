"""Stored settings take effect at pass boundaries in the spend consumers."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.declared_lever import DeclaredLever
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.harness.harness_set import HarnessAssemblyKey, HarnessSet, HarnessSets
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.lifecycle.spend_ceiling import SpendCeilingCheck
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.throttle.gate import ThrottleGate
from blizzard.runner.throttle.pause import USAGE_LIMIT_PARK_CAUSE, PausePark
from blizzard.runner.throttle.release import releases_between_steps, releases_usage_limited
from tests.support.composed_host import build_host_process
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import make_store, record_usage


def test_live_budget_agrees_with_host_and_runner_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    config = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    host = build_host_process(config, ready)
    hosted = build_hosted_app(config.host_view(), host)
    runner = host.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    settings = runner.stores.settings
    record_usage(
        store,
        lease_id="lease",
        chunk_id="ch",
        node_id="nd",
        epoch=1,
        generation=1,
        sample=UsageSample("spawn", "model", 0, 0, 0, 0, 7.0),
        recorded_at=datetime.now(UTC),
    )
    answers: list[bool] = []

    def observe(ctx: LoopContext) -> None:
        answers.append(ThrottleGate(ctx).verdict().admits_claim)

    monkeypatch.setattr(loop_wiring, "tick", observe)
    try:
        with TestClient(hosted.app) as client:
            for budget, expected in [(5, False), (10, True)]:
                settings.write(
                    Change("edit", sets={"throttle.budget.usd": budget, "throttle.on_decline": "release"}),
                    if_match=settings.snapshot().revision,
                    validate=reader.validate,
                    door="api",
                    actor="test",
                )
                assert hosted.steppers[0].request().outcome is SweepPassOutcome.COMPLETED
                host_status = client.get("/api/host/runners").json()["items"][0]["throttle"]
                runner_status = client.get(f"/runners/{TEST_RUNNER_ID}/api/runner").json()["throttle"]
                assert host_status == runner_status
                assert host_status["state"] == ("admitting" if expected else "declining")
                assert host_status["declining"] == ([] if expected else ["budget"])
                assert host_status["levers"][0]["name"] == "budget"
                assert host_status["on_decline"] == "release"
        assert answers == [False, True]
    finally:
        hosted.close()
        host.close()


def test_status_retains_pass_gate_through_pending_and_failed_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    config = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    host = build_host_process(config, ready)
    hosted = build_hosted_app(config.host_view(), host)
    runner = host.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    settings = runner.stores.settings
    record_usage(
        store,
        lease_id="lease",
        chunk_id="ch",
        node_id="nd",
        epoch=1,
        generation=1,
        sample=UsageSample("spawn", "model", 0, 0, 0, 0, 7.0),
        recorded_at=datetime.now(UTC),
    )
    observations: list[str] = []
    try:
        with TestClient(hosted.app) as client:

            def state() -> str:
                runner_status = client.get(f"/runners/{TEST_RUNNER_ID}/api/runner").json()["throttle"]
                assert client.get("/api/host/runners").json()["items"][0]["throttle"] == runner_status
                return str(runner_status["state"])

            def tick_with_write(ctx: LoopContext) -> None:
                assert ThrottleGate(ctx).verdict().admits_claim
                settings.write(
                    Change("edit", sets={"throttle.budget.usd": 5}),
                    if_match=settings.snapshot().revision,
                    validate=reader.validate,
                    door="api",
                    actor="test",
                )
                observations.append(state())

            monkeypatch.setattr(loop_wiring, "tick", tick_with_write)
            assert hosted.steppers[0].request().outcome is SweepPassOutcome.COMPLETED
            assert state() == "admitting"

            def fail_rebuild(_sets: HarnessSets, _key: HarnessAssemblyKey) -> HarnessSet:
                raise RuntimeError("rebuild failed")

            def idle_tick(_ctx: LoopContext) -> None:
                pass

            monkeypatch.setattr(loop_wiring, "tick", idle_tick)
            with monkeypatch.context() as failure:
                failure.setattr(HarnessSets, "prepare", fail_rebuild)
                assert hosted.steppers[0].request().outcome is SweepPassOutcome.COMPLETED
            assert hosted.steppers[0].settings_failure is not None
            assert state() == "admitting"
            assert hosted.steppers[0].request().outcome is SweepPassOutcome.COMPLETED
            assert state() == "declining"
        assert observations == ["admitting"]
    finally:
        hosted.close()
        host.close()


def test_live_spend_windows_reach_ceiling_and_budget_consumers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    config = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    host = build_host_process(config, ready)
    runner = host.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    settings = runner.stores.settings
    record_usage(
        store,
        lease_id="lease_old",
        chunk_id="ch_old",
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=UsageSample("spawn", "model", 0, 0, 0, 0, 7.0),
        recorded_at=datetime.now(UTC) - timedelta(hours=2),
    )
    observations: list[tuple[bool, bool]] = []

    def run(ctx: LoopContext) -> None:
        SpendCeilingCheck(ctx).run()
        observations.append((ctx.stores.pause.local_paused(), ThrottleGate(ctx).verdict().admits_claim))
        if len(observations) == 1:
            settings.write(
                Change(
                    "edit",
                    sets={
                        "cost.runner_ceiling_usd": 6,
                        "cost.window_hours": 1,
                        "throttle.budget.usd": 5,
                        "throttle.budget.window_hours": 1,
                    },
                ),
                if_match=settings.snapshot().revision,
                validate=reader.validate,
                door="api",
                actor="test",
            )

    monkeypatch.setattr(loop_wiring, "tick", run)
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        settings.write(
            Change("edit", sets={"cost.window_hours": 3, "throttle.budget.window_hours": 3}),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
    finally:
        stepper.close()
        host.close()
    assert observations == [(False, True), (False, True), (True, False)]


def test_live_schedule_changes_gate_answer_on_next_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    config = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    host = build_host_process(config, ready)
    runner = host.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    settings = runner.stores.settings
    answers: list[bool] = []

    def run(ctx: LoopContext) -> None:
        answers.append(ThrottleGate(ctx).verdict().admits_claim)
        if len(answers) == 1:
            tomorrow = (ctx.clock.now().astimezone(ctx.config.host_zone).weekday() + 1) % 7
            days = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
            settings.write(
                Change("edit", sets={"throttle.schedule.days": [days[tomorrow]]}),
                if_match=settings.snapshot().revision,
                validate=reader.validate,
                door="api",
                actor="test",
            )
            answers.append(ThrottleGate(ctx).verdict().admits_claim)

    monkeypatch.setattr(loop_wiring, "tick", run)
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
    finally:
        stepper.close()
        host.close()
    assert answers == [True, True, False]


def test_live_throttle_levers_and_release_policies_change_at_pass_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(
        tmp_path,
        top=f'db_url = "{url}"\n[[subscription]]\nslug = "plan-a"\nname = "Plan A"\nprovider = "anthropic"',
        runner='subscription = "plan-a"',
    )
    config = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    host = build_host_process(config, ready)
    runner = host.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    settings = runner.stores.settings
    observations: list[tuple[tuple[tuple[str, bool], ...], bool, bool]] = []
    park = PausePark("lease", "chunk", datetime.now(UTC), None, USAGE_LIMIT_PARK_CAUSE)

    def observe(ctx: LoopContext) -> None:
        verdict = ThrottleGate(ctx).verdict()
        observations.append(
            (
                tuple((lever.lever, lever.admits) for lever in verdict.levers),
                releases_between_steps(ctx.config.throttle, declined=True),
                releases_usage_limited(ctx.config.throttle, park, operator_holds=False, declined=True),
            )
        )
        if len(observations) == 1:
            settings.write(
                Change(
                    "edit",
                    sets={
                        "throttle.pace.lead_pct": 10,
                        "throttle.utilization.below_pct": 80,
                        "throttle.on_decline": "release",
                        "throttle.on_usage_limit": "release",
                    },
                ),
                if_match=settings.snapshot().revision,
                validate=reader.validate,
                door="api",
                actor="test",
            )
            verdict = ThrottleGate(ctx).verdict()
            observations.append(
                (
                    tuple((lever.lever, lever.admits) for lever in verdict.levers),
                    releases_between_steps(ctx.config.throttle, declined=True),
                    releases_usage_limited(ctx.config.throttle, park, operator_holds=False, declined=True),
                )
            )

    monkeypatch.setattr(loop_wiring, "tick", observe)
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
    finally:
        stepper.close()
        host.close()
    assert observations == [
        ((), False, False),
        ((), False, False),
        (((DeclaredLever.PACE, False), (DeclaredLever.UTILIZATION, False)), True, True),
    ]
