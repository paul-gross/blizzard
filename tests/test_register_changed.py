"""A registration reports a change only when it altered what the hub held, or found the runner offline."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.execution.fleet import Registered
from blizzard.hub.domain.runners.registration import STALE_AFTER, DeclaredSubscription, RunnerCapability
from tests.support import HubHarness, build_hub, seed_runner

pytestmark = pytest.mark.component

_RUNNER = "runner-a"


def _register(hub: HubHarness, **overrides: object) -> Registered:
    held = hub.services.registry.get_runner(_RUNNER)
    assert held is not None
    fields: dict[str, object] = {
        "name": "r-a",
        "env_capacity": 2,
        "public_url": "http://r-a",
        "redirect_uris": ("http://r-a/cb",),
        "capabilities": (RunnerCapability("claude", default=True),),
        "subscriptions": (DeclaredSubscription(slug="s", name="S", provider="p"),),
        "gates": ("g",),
        "projects": ("proj",),
    }
    fields.update(overrides)
    return hub.services.fleet.register(held, "w1", **fields)  # type: ignore[arg-type]


def _hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    seed_runner(hub, _RUNNER, register=False)
    return hub


def test_first_registration_is_a_change_and_an_identical_repeat_is_not(tmp_path: Path) -> None:
    hub = _hub(tmp_path)

    assert _register(hub).changed
    assert not _register(hub).changed


_CHANGES: dict[str, dict[str, object]] = {
    "name": {"name": "renamed"},
    "env_capacity": {"env_capacity": 3},
    "public_url": {"public_url": "http://other"},
    "redirect_uris": {"redirect_uris": ()},
    "capabilities": {"capabilities": ()},
    "subscriptions": {"subscriptions": None},
    "gates": {"gates": ()},
    "projects": {"projects": ()},
}


@pytest.mark.parametrize("field", _CHANGES)
def test_a_changed_field_is_a_change(tmp_path: Path, field: str) -> None:
    hub = _hub(tmp_path)
    _register(hub)

    assert _register(hub, **_CHANGES[field]).changed


def test_keep_semantics_are_not_a_change(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _register(hub)

    assert not _register(hub, name=None, projects=None).changed


def test_a_registration_from_an_offline_runner_is_a_change(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _register(hub)
    hub.clock.advance(STALE_AFTER + timedelta(seconds=1))

    assert _register(hub).changed
    assert not _register(hub).changed
