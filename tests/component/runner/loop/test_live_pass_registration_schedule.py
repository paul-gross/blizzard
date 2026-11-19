"""Stored pass settings reach registration and the scheduling gate."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.composition import build_host_process
from blizzard.runner.hub.client import HubClientError
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.throttle.gate import ThrottleGate
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import make_store


def test_live_schedule_hours_gates_and_subscription_reach_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(
        tmp_path,
        top=f'''db_url = "{url}"
[[subscription]]
slug = "plan-a"
name = "Plan A"
provider = "other"
''',
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
    sent: list[tuple[Any, Any, bool]] = []

    class Hub:
        def register_runner(self, name: str, workspace: str, **kwargs: Any) -> Any:
            sent.append((kwargs["gates"], kwargs["subscription"], current_gate[0]))
            raise HubClientError("offline")

    current_gate: list[bool] = []

    def observe(ctx: LoopContext) -> None:
        current_gate[:] = [ThrottleGate(ctx).verdict().admits_claim]
        Registration(replace(ctx, hub=cast(Any, Hub()))).run()

    def run(ctx: LoopContext) -> None:
        observe(ctx)
        if len(sent) == 1:
            hour = ctx.clock.now().astimezone(ctx.config.host_zone).hour
            start = (hour + 2) % 24
            end = (hour + 3) % 24
            settings.write(
                Change(
                    "edit",
                    sets={
                        "gates": ["review"],
                        "subscription": "plan-a",
                        "throttle.schedule.hours": [f"{start:02d}:00-{end:02d}:00"],
                    },
                ),
                if_match=settings.snapshot().revision,
                validate=reader.validate,
                door="api",
                actor="test",
            )
            observe(ctx)

    monkeypatch.setattr(loop_wiring, "tick", run)
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
    finally:
        stepper.close()
        host.close()
    assert sent == [((), None, True), ((), None, True), (("review",), "plan-a", False)]
