"""A live binding edit reaches the harness health probes, not only the loop config."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import blizzard.runner.composition as composition
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import make_store


def test_binding_change_rebuilds_health_probes_at_new_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    path = write_minimal_host(
        tmp_path,
        top=f'db_url = "{url}"',
        workspace='provider = "winter"\nroot = "first"',
        projects='[{project = "pr_a", workspace = "workspace-local"}]',
    )
    path.write_text(path.read_text() + '\n[[workspace]]\nid = "second"\nprovider = "winter"\nroot = "second"\n')
    host_config = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host_config.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host_config, clock=SystemClock())
    assert not halted
    roots: list[tuple[str, ...]] = []
    build = composition.build_production_harnesses

    def capture(*args: Any, **kwargs: Any) -> Any:
        roots.append(tuple(kwargs["spawn_roots"]))
        return build(*args, **kwargs)

    monkeypatch.setattr(composition, "build_production_harnesses", capture)
    host = composition.build_host_process(host_config, ready)
    runner = host.runners[0]
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        before = stepper._context()  # pyright: ignore[reportPrivateUsage]
        held = runner.harness_sets.retain("running")
        reader = runner.settings_reader
        assert reader is not None
        settings = runner.stores.settings
        settings.write(
            Change("edit", sets={"projects": [{"project": "pr_a", "workspace": "second"}]}),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        after = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert roots[-2:] == [("first",), ("second",)]
        assert after.harness_health is not before.harness_health
        assert runner.harness_sets.lookup("running") is held
    finally:
        stepper.close()
        host.close()
