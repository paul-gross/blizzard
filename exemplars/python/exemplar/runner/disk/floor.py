"""The disk-floor check and the loop context it declares."""

# exemplar: bzh:narrow-seams, bzh:steppable-loop — a loop step typed by the context Protocol it declares.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from exemplar.runner.disk.model import DiskBrakeFact, DiskFloorMove, IWriteDiskBrakeRepository, disk_floor_move
from exemplar.runner.disk.probe import DiskUnreadable, IDiskProbe

_log = get_logger("blizzard.runner.disk")


class DiskFloorStores(Protocol):
    @property
    def disk_brake(self) -> IWriteDiskBrakeRepository: ...


class DiskFloorConfig(Protocol):
    @property
    def spawn_root(self) -> Path: ...
    @property
    def disk_floor_bytes(self) -> int: ...


class DiskFloorContext(Protocol):
    """What the disk-floor check reads and writes."""

    @property
    def config(self) -> DiskFloorConfig: ...
    @property
    def stores(self) -> DiskFloorStores: ...
    @property
    def clock(self) -> IClock: ...
    @property
    def disk_probe(self) -> IDiskProbe: ...


@dataclass(frozen=True)
class DiskFloorCheck:
    """One disk-floor check of the spawn root; an unreadable filesystem leaves the brake as it stood."""

    ctx: DiskFloorContext

    def run(self) -> DiskFloorMove:
        config = self.ctx.config
        try:
            free = self.ctx.disk_probe.free_bytes(config.spawn_root)
        except DiskUnreadable:
            _log.warning("disk floor unread — brake left as it stood", path=str(config.spawn_root))
            return DiskFloorMove.STAND
        brake = self.ctx.stores.disk_brake
        move = disk_floor_move(brake.latest(), free_bytes=free, floor_bytes=config.disk_floor_bytes)
        if move is DiskFloorMove.STAND:
            return move
        engaged = move is DiskFloorMove.ENGAGE
        brake.record(DiskBrakeFact(engaged=engaged, free_bytes=free, at=self.ctx.clock.now()))
        if engaged:
            _log.warning("disk floor brake engaged", free_bytes=free, floor_bytes=config.disk_floor_bytes)
        else:
            _log.info("disk floor brake released", free_bytes=free, floor_bytes=config.disk_floor_bytes)
        return move
