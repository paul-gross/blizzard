"""Disk-probe selection: the one place a ``disk_probe`` name is read."""

# exemplar: bzh:pluggable-seams, bzh:dependency-injection — a name→builder registry over a settings object.

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from exemplar.runner.disk.probe import IDiskProbe


@domain_model
@dataclass(frozen=True)
class DiskProbeSettings:
    """What a disk probe is built from: the ``provider`` name."""

    provider: str


type DiskProbeBuilder = Callable[[DiskProbeSettings], IDiskProbe]


def _build_statvfs(settings: DiskProbeSettings) -> IDiskProbe:
    # Imported at build time: a driver loads only once it is the one selected.
    from exemplar.runner.disk.internal.statvfs_probe import StatvfsDiskProbe

    return StatvfsDiskProbe()


DISK_PROBES: Mapping[str, DiskProbeBuilder] = {"statvfs": _build_statvfs}


def build_disk_probe(settings: DiskProbeSettings) -> IDiskProbe:
    return DISK_PROBES[settings.provider](settings)
