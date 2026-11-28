"""Stored pass settings reach the hub registration at the next pass boundary."""

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
from blizzard.runner.hub.client import HubClientError
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from tests.support import composed_host
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import make_store


def test_registration_holds_name_capacity_and_bindings_through_inflight_edit_and_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(
        tmp_path,
        top=f'db_url = "{url}"',
        workspace=f'provider = "winter"\nroot = "{tmp_path / "workspace"}"',
        projects='[{project = "pr_a", workspace = "workspace-local"}]',
        agents=1,
    )
    host_config = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host_config.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host_config, clock=SystemClock())
    assert not halted
    host = composed_host.build_host_process(host_config, ready)
    runner = host.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    settings = runner.stores.settings
    sent: list[tuple[str, int | None, Any]] = []

    class Outage:
        def register_runner(self, name: str, workspace: str, **kwargs: Any) -> Any:
            sent.append((name, kwargs["agents"], kwargs["bindings"]))
            raise HubClientError("offline")

    def run_registration(ctx: LoopContext) -> None:
        registration = Registration(replace(ctx, hub=cast(Any, Outage())))
        registration.run()
        if len(sent) == 1:
            settings.write(
                Change(
                    "edit",
                    sets={
                        "name": "renamed",
                        "agents": 3,
                        "projects": [{"project": "pr_b", "workspace": "workspace-local"}],
                    },
                ),
                if_match=settings.snapshot().revision,
                validate=reader.validate,
                door="api",
                actor="test",
            )
            registration.run()

    monkeypatch.setattr(loop_wiring, "tick", run_registration)
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        assert stepper.request().outcome is SweepPassOutcome.COMPLETED
    finally:
        stepper.close()
        host.close()

    assert [(name, agents) for name, agents, _ in sent] == [("runner-local", 1), ("runner-local", 1), ("renamed", 3)]
    assert sent[0][2] == sent[1][2]
    assert sent[0][2][0].project == "pr_a"
    assert sent[2][2][0].project == "pr_b"
