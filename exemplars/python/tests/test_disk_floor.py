"""The disk-floor check — loop component tier: ``DiskFloorCheck(ctx).run()`` over fakes at the probe and brake seams."""

# exemplar: bzh:narrow-seams — a step component test with typed builders and sentinel-proved fakes.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from exemplar.runner.disk.floor import DiskFloorCheck
from exemplar.runner.disk.model import DiskBrakeFact, DiskFloorMove, IWriteDiskBrakeRepository
from exemplar.runner.disk.probe import DiskUnreadable, IDiskProbe
from exemplar.runner.loop.context import LoopConfig, LoopContext
from exemplar.runner.stores import RunnerStores

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
_ROOT = Path("/ws")
_FLOOR = 10 * 2**30
_UNDER = _FLOOR - 1
_OVER = _FLOOR * 2


@dataclass
class _FakeDiskProbe:
    free: int | None

    def free_bytes(self, path: Path) -> int:
        if self.free is None:
            raise DiskUnreadable(path)
        return self.free


@dataclass
class _FakeDiskBrakeRepository:
    facts: list[DiskBrakeFact] = field(default_factory=list[DiskBrakeFact])

    def latest(self) -> DiskBrakeFact | None:
        return self.facts[-1] if self.facts else None

    def record(self, fact: DiskBrakeFact) -> None:
        self.facts.append(fact)


def _conforms_fake_disk_probe(x: _FakeDiskProbe) -> IDiskProbe:
    return x


def _conforms_fake_disk_brake_repository(x: _FakeDiskBrakeRepository) -> IWriteDiskBrakeRepository:
    return x


def _ctx(probe: _FakeDiskProbe, brake: _FakeDiskBrakeRepository, clock: FixedClock) -> LoopContext:
    return LoopContext(
        stores=RunnerStores(disk_brake=brake),
        clock=clock,
        disk_probe=probe,
        config=LoopConfig(spawn_root=_ROOT, disk_floor_bytes=_FLOOR),
    )


def test_free_space_under_the_floor_engages_the_brake() -> None:
    brake = _FakeDiskBrakeRepository()

    move = DiskFloorCheck(_ctx(_FakeDiskProbe(_UNDER), brake, FixedClock(_NOW))).run()

    assert move is DiskFloorMove.ENGAGE
    assert brake.facts == [DiskBrakeFact(engaged=True, free_bytes=_UNDER, at=_NOW)]


def test_an_engaged_brake_stands_while_space_stays_low_and_releases_once_it_recovers() -> None:
    probe = _FakeDiskProbe(_UNDER)
    brake = _FakeDiskBrakeRepository()
    clock = FixedClock(_NOW)
    ctx = _ctx(probe, brake, clock)
    DiskFloorCheck(ctx).run()

    assert DiskFloorCheck(ctx).run() is DiskFloorMove.STAND
    assert len(brake.facts) == 1

    clock.advance(timedelta(minutes=5))
    probe.free = _OVER

    assert DiskFloorCheck(ctx).run() is DiskFloorMove.RELEASE
    assert brake.latest() == DiskBrakeFact(engaged=False, free_bytes=_OVER, at=_NOW + timedelta(minutes=5))


def test_space_above_the_floor_records_nothing_on_a_brake_that_never_moved() -> None:
    brake = _FakeDiskBrakeRepository()

    assert DiskFloorCheck(_ctx(_FakeDiskProbe(_OVER), brake, FixedClock(_NOW))).run() is DiskFloorMove.STAND
    assert brake.facts == []


def test_an_unreadable_filesystem_leaves_an_engaged_brake_engaged() -> None:
    probe = _FakeDiskProbe(_UNDER)
    brake = _FakeDiskBrakeRepository()
    ctx = _ctx(probe, brake, FixedClock(_NOW))
    DiskFloorCheck(ctx).run()
    probe.free = None

    assert DiskFloorCheck(ctx).run() is DiskFloorMove.STAND
    assert brake.latest() == DiskBrakeFact(engaged=True, free_bytes=_UNDER, at=_NOW)
