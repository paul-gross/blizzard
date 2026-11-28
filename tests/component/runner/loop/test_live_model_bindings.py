"""Live model policy edits bind to the actual harness resolution consumers."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from tests.support.composed_host import build_host_process
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import make_store


@pytest.mark.parametrize("prefix,harness", [("", "claude_code"), ("opencode.", "opencode")])
@pytest.mark.parametrize("policy", ["supported", "aliases", "effort"])
def test_live_model_policy_reaches_resolution_and_keeps_old_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prefix: str, harness: str, policy: str
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    host = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    stepper = RunnerStepper(runner.config, process_graph=runner)
    settings = runner.stores.settings

    def write(values: dict[str, object]) -> None:
        settings.write(
            Change("edit", sets=values),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )

    if policy == "supported":
        key, old, new = prefix + "models.supported", ["vendor/old"], ["vendor/new"]
    elif policy == "aliases":
        key, old, new = prefix + "models.aliases", {"custom": "vendor/old"}, {"custom": "vendor/new"}
    else:
        key, old, new = prefix + "effort.aliases", {"custom": "low"}, {"custom": "high"}
    try:
        write({key: old})
        before = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert before.harness_sets is not None
        old_registry = before.harness_sets.current.registry
        old_resolver = old_registry.model_resolution(harness)
        write({key: new})
        after = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert after.harness_sets is not None
        new_resolver = after.harness_sets.current.registry.model_resolution(harness)
        if policy == "supported":
            assert old_resolver.resolve_model_strict(["vendor/old"]) == "vendor/old"
            assert old_resolver.resolve_model_strict(["vendor/new"]) is None
            assert new_resolver.resolve_model_strict(["vendor/old"]) is None
            assert new_resolver.resolve_model_strict(["vendor/new"]) == "vendor/new"
        elif policy == "aliases":
            assert old_resolver.resolve_model_strict(["custom"]) == "vendor/old"
            assert new_resolver.resolve_model_strict(["custom"]) == "vendor/new"
        else:
            assert old_resolver.resolve_effort("custom") == "low"
            assert new_resolver.resolve_effort("custom") == "high"
    finally:
        stepper.close()
        graph.close()
