"""Free-space reads of a workspace's filesystem."""

# exemplar: bzh:pluggable-seams, bzh:comment-encapsulation — a driver seam with its error beside it.

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class IDiskProbe(Protocol):
    """Reads how much space a filesystem has free."""

    def free_bytes(self, path: Path) -> int:
        """Bytes free to an unprivileged writer on the filesystem holding ``path``.

        Raises :class:`DiskUnreadable` when the filesystem cannot be read."""
        ...


class DiskUnreadable(Exception):
    """The filesystem holding a path could not be read."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"cannot read free space at {path}")
        self.path = path
