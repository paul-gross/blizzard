"""The runner's lever names are the shared vocabulary's values — the two cannot drift."""

from __future__ import annotations

from blizzard.foundation.declared_lever import DeclaredLever
from blizzard.runner.throttle.levers import LeverName
from blizzard.runner.throttle.report import FLOOR_LEVER


def test_every_runner_lever_name_is_a_declared_lever() -> None:
    declared = {lever.value for lever in DeclaredLever}

    assert {lever.value for lever in LeverName} <= declared
    assert FLOOR_LEVER in declared
    assert declared == {lever.value for lever in LeverName} | {FLOOR_LEVER}
