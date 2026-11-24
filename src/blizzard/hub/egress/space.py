"""The one measure of a directory's free space."""

from __future__ import annotations

import shutil
from pathlib import Path

__all__ = ["DirectoryFreeSpace", "free_bytes"]


def free_bytes(directory: Path) -> int | None:
    """Bytes free on ``directory``'s filesystem, or ``None`` when it cannot be read."""
    try:
        return shutil.disk_usage(directory).free
    except OSError:
        return None


class DirectoryFreeSpace:
    """The export directory's free space; ``None`` with no directory set."""

    def __init__(self, directory: Path | None) -> None:
        self._directory = directory

    def __call__(self) -> int | None:
        return free_bytes(self._directory) if self._directory is not None else None
