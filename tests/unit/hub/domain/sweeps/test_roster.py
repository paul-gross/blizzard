"""The hub's sweep roster: its names and modes, the locks a pass holds, and one pass over every tenant or over one,
run by the timer or on request alike."""

from __future__ import annotations

import pytest

from blizzard.foundation.config_keys import ConfigView
from blizzard.hub.config import ConfigError, parse_sweep_modes
from blizzard.hub.domain.sweeps.locks import SweepInProgress, SweepLock, SweepLocks, admits
from blizzard.hub.domain.sweeps.names import SweepMode, SweepModes, SweepName
from tests.support_sweep_roster import A, B


def test_each_sweep_name_knows_its_logger_segment_and_whether_one_tenant_serves_it() -> None:
    assert {name: name.logger_segment for name in SweepName} == {
        SweepName.FORGE_STATUS: "forge_status",
        SweepName.EVENT_DERIVATION: "transcript_events",
        SweepName.CLOSE_DRAIN: "work_closure",
        SweepName.TENANT_TEARDOWN: "tenant_teardown",
        SweepName.TRACE_EXPORT: "trace_export",
        SweepName.EGRESS: "egress",
    }
    assert {name for name in SweepName if name.serves_one_tenant()} == {
        SweepName.FORGE_STATUS,
        SweepName.EVENT_DERIVATION,
        SweepName.CLOSE_DRAIN,
    }


def test_every_sweep_is_timed_unless_its_table_says_stepped() -> None:
    modes = parse_sweep_modes(ConfigView.unchecked({"close_drain": {"mode": "stepped"}, "egress": {}}))

    assert {name: modes.of(name) for name in SweepName} == {
        **dict.fromkeys(SweepName, SweepMode.TIMED),
        SweepName.CLOSE_DRAIN: SweepMode.STEPPED,
    }
    assert SweepModes().of(SweepName.CLOSE_DRAIN) is SweepMode.TIMED


@pytest.mark.parametrize("mode", ["bogus", 5, ["a"], {"a": 1}])
def test_a_mode_outside_timed_and_stepped_is_refused_naming_its_key(mode: object) -> None:
    with pytest.raises(ConfigError, match=r"sweeps\.close_drain\.mode"):
        parse_sweep_modes(ConfigView.unchecked({"close_drain": {"mode": mode}}))


_EVERY: SweepLock = (SweepName.CLOSE_DRAIN, None)


_IN_A: SweepLock = (SweepName.CLOSE_DRAIN, A)


_IN_B: SweepLock = (SweepName.CLOSE_DRAIN, B)


_OTHER: SweepLock = (SweepName.EVENT_DERIVATION, None)


@pytest.mark.parametrize(
    ("held", "lock", "admitted"),
    [
        (set[SweepLock](), _EVERY, True),
        ({_OTHER}, _EVERY, True),
        ({_EVERY}, _EVERY, False),
        ({_IN_A}, _EVERY, False),
        (set[SweepLock](), _IN_A, True),
        ({_IN_B}, _IN_A, True),
        ({_OTHER}, _IN_A, True),
        ({_IN_A}, _IN_A, False),
        ({_EVERY}, _IN_A, False),
    ],
)
def test_the_admit_rule(held: set[SweepLock], lock: SweepLock, admitted: bool) -> None:
    assert admits(held, lock) is admitted


def test_a_held_lock_is_refused_at_once_and_released_when_its_pass_ends() -> None:
    locks = SweepLocks()

    with locks.hold(SweepName.CLOSE_DRAIN, A), pytest.raises(SweepInProgress), locks.hold(SweepName.CLOSE_DRAIN):
        pass  # pragma: no cover - refused
    with locks.hold(SweepName.CLOSE_DRAIN):
        pass
