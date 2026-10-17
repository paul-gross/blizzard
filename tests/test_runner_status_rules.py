"""The runner status read model's rules, pinned by value: effective pause, free capacity,
hub reachability, and the environment-slot derivation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.environments.repository import OpenLeaseHold
from blizzard.runner.hub.client import BindingDeclaration
from blizzard.runner.hub.served_projects import ServedProject, ServedProjectsAnswer
from blizzard.runner.lifecycle.routing import routing_map
from blizzard.runner.status.view import BindingCapacity, Capacities, HubConnectivity, PauseState

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
_AT = _NOW


# --- PauseState.of ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("local", "hub", "effective"), [(False, False, False), (True, False, True), (False, True, True), (True, True, True)]
)
def test_either_brake_pauses_the_runner(local: bool, hub: bool, effective: bool) -> None:
    assert PauseState.of(local=local, hub=hub, local_reason=None).effective is effective


def test_the_local_reason_is_kept_only_while_the_local_brake_is_on() -> None:
    assert PauseState.of(local=True, hub=False, local_reason="usage limit").local_reason == "usage limit"
    assert PauseState.of(local=False, hub=True, local_reason="usage limit").local_reason is None


# --- Capacities.of ----------------------------------------------------------------------


_BINDINGS = (BindingDeclaration("a", "ws1", 4), BindingDeclaration("b", "ws2", 2))
_ROUTING = routing_map(
    _BINDINGS,
    ServedProjectsAnswer(
        projects=(ServedProject("a", project_id="pr_a"), ServedProject("b", project_id="pr_b")), answered_at=_AT
    ),
)


def test_each_binding_reports_what_remains_of_its_own_agents() -> None:
    holds = [OpenLeaseHold("ch_1", held=True, workspace_id="ws1", project_id="pr_a")]
    assert Capacities.of(_BINDINGS, _ROUTING, holds) == Capacities(
        used=1,
        bindings=(
            BindingCapacity(project="a", project_id="pr_a", workspace_id="ws1", agents=4, used=1, free=3),
            BindingCapacity(project="b", project_id="pr_b", workspace_id="ws2", agents=2, used=0, free=2),
        ),
    )


def test_free_never_reads_below_zero() -> None:
    holds = [OpenLeaseHold(f"ch_{n}", held=False) for n in range(5)]
    capacities = Capacities.of(_BINDINGS, _ROUTING, holds)
    assert capacities.used == 5
    assert [b.free for b in capacities.bindings] == [0, 0]


def test_an_unresolved_binding_reports_no_project_id() -> None:
    capacities = Capacities.of(_BINDINGS, routing_map(_BINDINGS, None), [])
    assert [b.project_id for b in capacities.bindings] == [None, None]


# --- HubConnectivity.of -----------------------------------------------------------------


def _hub(contact_at: datetime | None) -> HubConnectivity:
    return HubConnectivity.of(
        endpoint="https://hub", contact_at=contact_at, buffer_depth=3, now=_NOW, threshold=timedelta(minutes=5)
    )


def test_a_contact_within_the_threshold_is_reachable() -> None:
    assert _hub(_NOW - timedelta(minutes=5)).reachable is True


def test_a_contact_past_the_threshold_is_unreachable() -> None:
    assert _hub(_NOW - timedelta(minutes=5, seconds=1)).reachable is False


def test_never_contacted_is_unreachable_and_carries_the_rest_through() -> None:
    assert _hub(None) == HubConnectivity(endpoint="https://hub", reachable=False, last_contact_at=None, buffer_depth=3)
