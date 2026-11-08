"""A registration repeating a harness id is refused whole, before any write."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.runners.registration import (
    DuplicateCapabilityHarness,
    IWriteRunnerRegistry,
    RunnerCapability,
    RunnerRegistration,
    RunnerRetired,
)
from blizzard.hub.domain.runners.version import RunnerVersionRefused
from blizzard.hub.domain.runners.version_window import VersionWindow
from tests.support.scaffolding import HubHarness, build_hub, seed_runner

_RUNNER = "runner-a"
_IDENTICAL = (RunnerCapability("claude"), RunnerCapability("claude"))
_CONFLICTING = (RunnerCapability("claude", version="1"), RunnerCapability("claude", version="2", available=False))
_PRIOR = (RunnerCapability("claude", default=True), RunnerCapability("opencode"))


def _held(hub: HubHarness) -> RunnerRegistration:
    registration = hub.services.registry.get_runner(_RUNNER)
    assert registration is not None
    return registration


def _hub(tmp_path: Path, *, prior: bool, window: VersionWindow | None = None) -> HubHarness:
    hub = build_hub(tmp_path) if window is None else build_hub(tmp_path, hub_version="0.3", version_window=window)
    seed_runner(hub, _RUNNER, register=False)
    if prior:
        hub.services.fleet.register(_held(hub), "ws-a", env_capacity=2, capabilities=_PRIOR)
    return hub


@pytest.mark.parametrize("duplicates", [_IDENTICAL, _CONFLICTING], ids=["identical", "conflicting"])
@pytest.mark.parametrize("prior", [False, True], ids=["never-registered", "prior-snapshot"])
def test_a_duplicate_snapshot_is_refused_and_changes_nothing(
    tmp_path: Path, duplicates: tuple[RunnerCapability, ...], prior: bool
) -> None:
    hub = _hub(tmp_path, prior=prior)
    before = _held(hub)

    with pytest.raises(DuplicateCapabilityHarness) as refused:
        hub.services.fleet.register(_held(hub), "ws-b", env_capacity=9, capabilities=duplicates, projects=[])

    assert refused.value.field == "capabilities[1].harness_id"
    assert refused.value.harness_id == "claude"
    assert _held(hub) == before


def test_a_retired_runner_with_duplicates_is_refused_as_retired(tmp_path: Path) -> None:
    hub = _hub(tmp_path, prior=True)
    writer = cast(IWriteRunnerRegistry, hub.services.registry)
    writer.record_lifecycle(_RUNNER, retired=True, at=hub.clock.now(), by="op", expected_lifecycle=0)

    with pytest.raises(RunnerRetired):
        hub.services.fleet.register(_held(hub), "ws-a", capabilities=_IDENTICAL)


def test_an_out_of_window_version_with_duplicates_is_refused_on_version(tmp_path: Path) -> None:
    hub = _hub(tmp_path, prior=False, window=VersionWindow(floor="0.2"))

    with pytest.raises(RunnerVersionRefused):
        hub.services.fleet.register(_held(hub), "ws-a", capabilities=_IDENTICAL, version="0.1.4")

    assert _held(hub).reported_version == "0.1.4"


def test_a_unique_snapshot_replaces_the_prior_one_and_an_empty_one_clears_it(tmp_path: Path) -> None:
    hub = _hub(tmp_path, prior=True)

    hub.services.fleet.register(_held(hub), "ws-a", capabilities=(RunnerCapability("opencode"),))
    assert [c.harness_id for c in _held(hub).capabilities] == ["opencode"]

    hub.services.fleet.register(_held(hub), "ws-a", capabilities=())
    assert _held(hub).capabilities == ()
