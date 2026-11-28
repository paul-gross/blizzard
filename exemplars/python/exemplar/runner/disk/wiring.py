"""The disk area's host registrations: the probe it selects and the host's free-space reader, once per host.

The reader takes the host's clock and host file by type; the area names neither's provider."""

# exemplar: bzh:dependency-injection — a new host service is born here, one line, and the host root calls ``register``.

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Registry
from exemplar.runner.disk.factory import DiskProbeSettings, build_disk_probe
from exemplar.runner.disk.probe import IDiskProbe
from exemplar.runner.disk.reading import HostDiskReader

#: The probe the host reads its root's free space with.
_PROBE = DiskProbeSettings(provider="statvfs")


def _probe() -> IDiskProbe:
    return build_disk_probe(_PROBE)


def register(registry: Registry) -> None:
    """Declare the disk probe and the host's free-space reader, each once per host."""
    registry.factory(_probe, lifetime=Lifetime.PROCESS)
    registry.service(HostDiskReader, lifetime=Lifetime.PROCESS)
