"""The host's free-space reading of its own root."""

# exemplar: bzh:dependency-injection — a host service names each collaborator it takes and builds none.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.runner.config import RunnerConfig
from exemplar.runner.disk.probe import IDiskProbe


@domain_model
@dataclass(frozen=True)
class DiskReading:
    """The free space on the host root's filesystem, and when it was read."""

    free_bytes: int
    at: datetime


class HostDiskReader:
    """Reads the free space on the filesystem holding the host's root."""

    def __init__(self, probe: IDiskProbe, clock: IClock, config: RunnerConfig) -> None:
        self._probe = probe
        self._clock = clock
        self._root = config.root

    def read(self) -> DiskReading:
        """Raises :class:`~exemplar.runner.disk.probe.DiskUnreadable` when the filesystem cannot be read."""
        return DiskReading(free_bytes=self._probe.free_bytes(self._root), at=self._clock.now())
