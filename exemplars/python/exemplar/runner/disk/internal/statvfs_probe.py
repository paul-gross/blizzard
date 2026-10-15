"""The ``statvfs``-backed disk probe."""

# exemplar: bzh:internal-visibility, bzh:comment-encapsulation — a bare adapter in its package's ``internal/``.

from __future__ import annotations

import os
from pathlib import Path

from exemplar.runner.disk.probe import DiskUnreadable, IDiskProbe


class StatvfsDiskProbe:
    def free_bytes(self, path: Path) -> int:
        try:
            stat = os.statvfs(path)
        except OSError as exc:
            raise DiskUnreadable(path) from exc
        return stat.f_bavail * stat.f_frsize


def _conforms_statvfs_disk_probe(x: StatvfsDiskProbe) -> IDiskProbe:
    return x
