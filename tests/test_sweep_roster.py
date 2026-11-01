"""The hub's sweep roster: its names and modes, the locks a pass holds, and one pass over every tenant or over one,
run by the timer or on request alike."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.hub.app import Sweep, lifespan
from blizzard.hub.config import ConfigError, parse_sweep_modes
from blizzard.hub.domain.kernel.tenant_fault import TenantFault
from blizzard.hub.domain.sweeps.locks import SweepInProgress, SweepLock, SweepLocks, admits
from blizzard.hub.domain.sweeps.names import SweepMode, SweepModes, SweepName
from blizzard.hub.domain.sweeps.roster import SweepRoster, SweepServesNoTenant, UnknownSweep
from blizzard.hub.tenancy import TenantId
from tests.config_inputs import load_hub_config
from tests.support import build_hub

_A = TenantId("ten_a")
_B = TenantId("ten_b")
_NOW = datetime(2026, 7, 13, tzinfo=UTC)


# --- names, modes and the admit rule -------------------------------------------------------


@pytest.mark.unit
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


@pytest.mark.unit
def test_every_sweep_is_timed_unless_its_table_says_stepped() -> None:
    modes = parse_sweep_modes(ConfigView.unchecked({"close_drain": {"mode": "stepped"}, "egress": {}}))

    assert {name: modes.of(name) for name in SweepName} == {
        **dict.fromkeys(SweepName, SweepMode.TIMED),
        SweepName.CLOSE_DRAIN: SweepMode.STEPPED,
    }
    assert SweepModes().of(SweepName.CLOSE_DRAIN) is SweepMode.TIMED


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["bogus", 5, ["a"], {"a": 1}])
def test_a_mode_outside_timed_and_stepped_is_refused_naming_its_key(mode: object) -> None:
    with pytest.raises(ConfigError, match=r"sweeps\.close_drain\.mode"):
        parse_sweep_modes(ConfigView.unchecked({"close_drain": {"mode": mode}}))


_EVERY: SweepLock = (SweepName.CLOSE_DRAIN, None)
_IN_A: SweepLock = (SweepName.CLOSE_DRAIN, _A)
_IN_B: SweepLock = (SweepName.CLOSE_DRAIN, _B)
_OTHER: SweepLock = (SweepName.EVENT_DERIVATION, None)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("held", "lock", "admitted"),
    [
        (set(), _EVERY, True),
        ({_OTHER}, _EVERY, True),
        ({_EVERY}, _EVERY, False),
        ({_IN_A}, _EVERY, False),
        (set(), _IN_A, True),
        ({_IN_B}, _IN_A, True),
        ({_OTHER}, _IN_A, True),
        ({_IN_A}, _IN_A, False),
        ({_EVERY}, _IN_A, False),
    ],
)
def test_the_admit_rule(held: set[SweepLock], lock: SweepLock, admitted: bool) -> None:
    assert admits(held, lock) is admitted


@pytest.mark.unit
def test_a_held_lock_is_refused_at_once_and_released_when_its_pass_ends() -> None:
    locks = SweepLocks()

    with locks.hold(SweepName.CLOSE_DRAIN, _A), pytest.raises(SweepInProgress), locks.hold(SweepName.CLOSE_DRAIN):
        pass  # pragma: no cover - refused
    with locks.hold(SweepName.CLOSE_DRAIN):
        pass


# --- the roster ----------------------------------------------------------------------------


class _Reconciler:
    def __init__(self, clock: FixedClock, *, raises: bool = False, fault: TenantFault | None = None) -> None:
        self.clock = clock
        self.raises = raises
        self.fault = fault
        self.passes: list[TenantId | None] = []

    def sweep(self) -> None:
        self.passes.append(None)
        self.clock.advance(timedelta(seconds=3))
        if self.raises:
            raise RuntimeError("the corpus cannot be read")

    def sweep_tenant(self, tenant: TenantId) -> TenantFault | None:
        self.passes.append(tenant)
        self.clock.advance(timedelta(seconds=2))
        return self.fault


def _roster(reconciler: _Reconciler, clock: FixedClock, locks: SweepLocks | None = None) -> SweepRoster:
    return SweepRoster(
        every_tenant={SweepName.CLOSE_DRAIN: reconciler, SweepName.TENANT_TEARDOWN: reconciler},
        one_tenant={SweepName.CLOSE_DRAIN: reconciler},
        locks=locks or SweepLocks(),
        clock=clock,
    )


@pytest.mark.component
def test_a_pass_over_every_tenant_is_stamped_from_the_hubs_clock_and_completes() -> None:
    clock = FixedClock(_NOW)
    reconciler = _Reconciler(clock)

    result = _roster(reconciler, clock).pass_every_tenant("close_drain")

    assert reconciler.passes == [None]
    assert (result.started_at, result.finished_at) == (_NOW, _NOW + timedelta(seconds=3))
    assert (result.outcome, result.error) == (SweepPassOutcome.COMPLETED, None)


@pytest.mark.component
def test_a_pass_over_every_tenant_that_raises_is_logged_and_fails_with_its_message() -> None:
    clock = FixedClock(_NOW)

    with capture_logs() as logs:
        result = _roster(_Reconciler(clock, raises=True), clock).pass_every_tenant("close_drain")

    assert (result.outcome, result.error) == (SweepPassOutcome.FAILED, "the corpus cannot be read")
    assert [entry["event"] for entry in logs] == ["sweep failed"]


@pytest.mark.component
def test_a_pass_over_one_tenant_runs_that_tenants_pass_and_fails_with_its_fault() -> None:
    clock = FixedClock(_NOW)
    reconciler = _Reconciler(clock, fault=TenantFault("the forge refused the close"))

    result = _roster(reconciler, clock).pass_one_tenant("close_drain", _A)

    assert reconciler.passes == [_A]
    assert (result.started_at, result.finished_at) == (_NOW, _NOW + timedelta(seconds=2))
    assert (result.outcome, result.error) == (SweepPassOutcome.FAILED, "the forge refused the close")


@pytest.mark.component
def test_an_unknown_or_unbuilt_name_is_refused_before_any_other_check() -> None:
    clock = FixedClock(_NOW)
    roster = _roster(_Reconciler(clock), clock)

    for name in ("nope", "trace_export"):
        with pytest.raises(UnknownSweep):
            roster.pass_every_tenant(name)
        with pytest.raises(UnknownSweep):
            roster.pass_one_tenant(name, _A)


@pytest.mark.component
def test_a_sweep_over_every_tenant_only_refuses_a_one_tenant_pass() -> None:
    clock = FixedClock(_NOW)

    with pytest.raises(SweepServesNoTenant):
        _roster(_Reconciler(clock), clock).pass_one_tenant("tenant_teardown", _A)


@pytest.mark.component
def test_a_pass_whose_lock_is_held_is_refused_and_runs_nothing() -> None:
    clock, locks = FixedClock(_NOW), SweepLocks()
    reconciler = _Reconciler(clock)
    roster = _roster(reconciler, clock, locks)

    with locks.hold(SweepName.CLOSE_DRAIN, _A):
        with pytest.raises(SweepInProgress):
            roster.pass_every_tenant("close_drain")
        with pytest.raises(SweepInProgress):
            roster.pass_one_tenant("close_drain", _A)
        roster.pass_one_tenant("close_drain", _B)

    assert reconciler.passes == [_B]


# --- the timed driver over a real hub ------------------------------------------------------


@pytest.mark.component
def test_the_hubs_roster_holds_every_sweep_it_built(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert hub.hub_services.sweeps.names() == [
        SweepName.FORGE_STATUS,
        SweepName.EVENT_DERIVATION,
        SweepName.CLOSE_DRAIN,
        SweepName.TENANT_TEARDOWN,
    ]


@pytest.mark.component
def test_a_stepped_sweep_runs_no_pass_at_boot_or_after_its_interval(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    hub.app.state.config = dataclasses.replace(
        hub.app.state.config,
        sweeps=SweepModes({SweepName.CLOSE_DRAIN: SweepMode.STEPPED, SweepName.EVENT_DERIVATION: SweepMode.STEPPED}),
    )
    assert [sweep.config_name for sweep in Sweep.all(hub.app)] == [
        SweepName.FORGE_STATUS,
        SweepName.TENANT_TEARDOWN,
    ]

    async def boot() -> None:
        async with lifespan(hub.app):  # type: ignore[arg-type]
            await asyncio.sleep(0.3)

    with capture_logs() as logs:
        asyncio.run(boot())

    completed = {entry["event"] for entry in logs}
    assert "forge-status sweep completed" in completed or "forge-status sweep skipped" in completed
    assert "close intent drain sweep completed" not in completed
    assert not any(entry["event"].startswith("transcript event derivation") for entry in logs)


@pytest.mark.component
def test_a_mode_outside_timed_and_stepped_refuses_the_hubs_boot(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    (root / "data").mkdir(parents=True)
    (root / "blizzard-hub.toml").write_text('[sweeps.close_drain]\nmode = "bogus"\n')

    with pytest.raises(ConfigError, match=r"sweeps\.close_drain\.mode"):
        load_hub_config(root)
