"""The status throttle block's rendering, pinned line by line: a held floor in each of its shapes, a hand-cleared
floor, and a throttle with no levers. Populated levers are pinned with the gate's own tests."""

from __future__ import annotations

from typing import Any, cast

from blizzard.runner.cli.control import Throttle, throttle_lines

_TAIL = "    on_decline: release; on_usage_limit: hold"


def _throttle(floor: dict[str, Any]) -> Throttle:
    return cast(
        Throttle,
        {
            "floor": floor,
            "state": "declining",
            "declining": ["floor"],
            "on_decline": "release",
            "on_usage_limit": "hold",
        },
    )


def test_a_held_floor_names_its_window_and_the_grace_that_opens_it() -> None:
    hold = {
        "kind": "usage_limit",
        "window": "5h",
        "grace_opens_at": "2026-10-01T13:00:00Z",
        "expected_reset": "2026-10-01T14:00:00Z",
        "awaits_sample": False,
    }
    assert throttle_lines(_throttle({"admits": False, "subscription": "team-plan", "hold": hold})) == [
        "  throttle: declining (declining: floor)",
        "    floor (team-plan): hold — usage_limit (5h), until 2026-10-01T13:00:00Z",
        _TAIL,
    ]


def test_a_held_floor_without_grace_runs_until_the_expected_reset_and_names_the_harness() -> None:
    hold = {
        "kind": "usage_limit",
        "harness_id": "claude",
        "expected_reset": "2026-10-01T14:00:00Z",
        "awaits_sample": False,
    }
    assert throttle_lines(_throttle({"admits": False, "hold": hold})) == [
        "  throttle: declining (declining: floor)",
        "    floor (runner): hold — usage_limit (claude), until 2026-10-01T14:00:00Z",
        _TAIL,
    ]


def test_a_held_floor_with_neither_window_nor_harness_reads_as_a_bare_usage_limit() -> None:
    hold = {"kind": "usage_limit", "expected_reset": "2026-10-01T14:00:00Z", "awaits_sample": False}
    assert throttle_lines(_throttle({"admits": False, "hold": hold}))[1] == (
        "    floor (runner): hold — usage_limit (usage limit), until 2026-10-01T14:00:00Z"
    )


def test_a_hold_awaiting_a_sample_says_so_after_its_reset() -> None:
    hold = {
        "kind": "usage_limit",
        "window": "5h",
        "grace_opens_at": "2026-10-01T13:00:00Z",
        "expected_reset": "2026-10-01T14:00:00Z",
        "awaits_sample": True,
    }
    assert throttle_lines(_throttle({"admits": False, "hold": hold}))[1] == (
        "    floor (runner): hold — usage_limit (5h), until 2026-10-01T13:00:00Z;"
        " reset 2026-10-01T14:00:00Z, awaiting sample"
    )


def test_a_hand_cleared_floor_names_who_cleared_it_and_when() -> None:
    floor = {"admits": True, "clear": {"by": "paul", "cleared_at": "2026-10-01T12:00:00Z"}}
    assert (
        throttle_lines(_throttle(floor))[1] == "    floor (runner): admit — hand clear by paul at 2026-10-01T12:00:00Z"
    )


def test_a_throttle_with_no_levers_renders_the_headline_floor_and_policy_only() -> None:
    throttle = _throttle({"admits": True})
    del throttle["declining"]
    assert throttle_lines(throttle) == [
        "  throttle: declining (declining: none)",
        "    floor (runner): admit — open",
        _TAIL,
    ]
