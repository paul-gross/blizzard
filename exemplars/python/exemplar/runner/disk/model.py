"""The disk brake — its facts, the move one check makes on it, and its repository ports."""

# exemplar: bzh:domain-orchestration-split, bzh:facts-not-status — the move as a pure function of loaded values.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model


class DiskFloorMove(StrEnum):
    """What one disk-floor check does to the brake."""

    ENGAGE = "engage"
    RELEASE = "release"
    #: The brake already matches the free space it read.
    STAND = "stand"


@domain_model
@dataclass(frozen=True)
class DiskBrakeFact:
    """One appended brake transition and the free space that moved it."""

    engaged: bool
    free_bytes: int
    at: datetime


def disk_floor_move(latest: DiskBrakeFact | None, *, free_bytes: int, floor_bytes: int) -> DiskFloorMove:
    """The fill is braked while free space is under the floor and released once it is back at or above it."""
    engaged = latest is not None and latest.engaged
    under = free_bytes < floor_bytes
    if under and not engaged:
        return DiskFloorMove.ENGAGE
    if engaged and not under:
        return DiskFloorMove.RELEASE
    return DiskFloorMove.STAND


class IReadDiskBrakeRepository(Protocol):
    """Read-only disk-brake access."""

    def latest(self) -> DiskBrakeFact | None:
        """The newest brake fact, or ``None`` when the brake has never moved."""
        ...


class IWriteDiskBrakeRepository(IReadDiskBrakeRepository, Protocol):
    """Adds the disk-brake write."""

    def record(self, fact: DiskBrakeFact) -> None: ...
