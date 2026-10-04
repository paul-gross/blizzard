"""The runner status read model's rules, pinned by value: effective pause, free capacity,
hub reachability, and the environment-slot derivation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.status.view import Capacities, EnvironmentSlot, HubConnectivity, PauseState

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


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


def test_free_is_what_remains_of_the_maximum() -> None:
    assert Capacities.of(max_agents=4, used=1) == Capacities(max_agents=4, used=1, free=3)


def test_free_never_reads_below_zero() -> None:
    assert Capacities.of(max_agents=2, used=5).free == 0


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


# --- EnvironmentSlot.pool_view ----------------------------------------------------------


def _binding(env: str, chunk: str) -> EnvBinding:
    return EnvBinding(chunk_id=chunk, environment_id=env, workdir=f"/ws/{env}", bound_at=_NOW)


def test_every_pool_environment_surfaces_held_or_not() -> None:
    assert EnvironmentSlot.pool_view(["e1", "e2"], [_binding("e2", "ch_1")]) == [
        EnvironmentSlot(environment_id="e1", chunk_id=None, held_since=None),
        EnvironmentSlot(environment_id="e2", chunk_id="ch_1", held_since=_NOW),
    ]


def test_extra_bindings_on_one_id_follow_the_first_as_their_own_rows() -> None:
    slots = EnvironmentSlot.pool_view(["e1", "e2"], [_binding("e1", "ch_1"), _binding("e1", "ch_2")])
    assert [(s.environment_id, s.chunk_id) for s in slots] == [("e1", "ch_1"), ("e1", "ch_2"), ("e2", None)]


def test_a_binding_outside_the_pool_is_appended() -> None:
    slots = EnvironmentSlot.pool_view(["e1"], [_binding("gone", "ch_9"), _binding("e1", "ch_1")])
    assert [(s.environment_id, s.chunk_id) for s in slots] == [("e1", "ch_1"), ("gone", "ch_9")]


def test_a_slot_is_held_exactly_while_a_chunk_is_bound_to_it() -> None:
    assert [slot.is_held() for slot in EnvironmentSlot.pool_view(["e1", "e2"], [_binding("e2", "ch_1")])] == [
        False,
        True,
    ]
