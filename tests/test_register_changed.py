"""A registration reports a change only when it altered what the hub held, or found the runner offline."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict, Unpack

import pytest

from blizzard.hub.domain.execution.fleet import Registered
from blizzard.hub.domain.runners.registration import (
    STALE_AFTER,
    DeclaredSubscription,
    RunnerBinding,
    RunnerCapability,
    RunnerDeclining,
)
from tests.support import HubHarness, build_hub, seed_runner

pytestmark = pytest.mark.component

_RUNNER = "runner-a"


class _RegisterFields(TypedDict, total=False):
    name: str | None
    env_capacity: int | None
    public_url: str | None
    redirect_uris: tuple[str, ...]
    capabilities: tuple[RunnerCapability, ...]
    subscriptions: tuple[DeclaredSubscription, ...] | None
    gates: tuple[str, ...]
    projects: tuple[str, ...] | None
    bindings: tuple[RunnerBinding, ...] | None
    subscription: str | None
    declining: tuple[RunnerDeclining, ...] | None


def _register(hub: HubHarness, **overrides: Unpack[_RegisterFields]) -> Registered:
    held = hub.services.registry.get_runner(_RUNNER)
    assert held is not None
    fields: _RegisterFields = {
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
    return hub.services.fleet.register(held, "w1", **fields)


def _hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    seed_runner(hub, _RUNNER, register=False)
    return hub


def test_first_registration_is_a_change_and_an_identical_repeat_is_not(tmp_path: Path) -> None:
    hub = _hub(tmp_path)

    assert _register(hub).changed
    assert not _register(hub).changed


_CHANGES: dict[str, _RegisterFields] = {
    "name": {"name": "renamed"},
    "env_capacity": {"env_capacity": 3},
    "public_url": {"public_url": "http://other"},
    "redirect_uris": {"redirect_uris": ()},
    "capabilities": {"capabilities": ()},
    "subscriptions": {"subscriptions": None},
    "gates": {"gates": ()},
    "projects": {"projects": ()},
    "bindings": {"bindings": (RunnerBinding("proj", "w1", 2),)},
    "subscription": {"subscription": "s"},
    "declining": {"declining": (RunnerDeclining("floor", "exhausted", "anthropic"),)},
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


def test_a_runner_that_sends_no_declining_report_and_one_that_sends_none_declining_agree(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _register(hub, declining=None)

    assert not _register(hub, declining=()).changed
    assert not _register(hub, declining=None).changed


def test_a_repeated_reason_is_not_a_change_and_a_lifted_one_is(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    reason = (RunnerDeclining("floor", "exhausted", "anthropic", datetime(2026, 10, 22, 14, tzinfo=UTC)),)
    _register(hub, declining=reason)

    assert not _register(hub, declining=reason).changed
    assert _register(hub, declining=()).changed
