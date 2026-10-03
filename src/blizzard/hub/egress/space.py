"""The one measure of a directory's free space — the writer's disk guard and the operator's status read it alike."""

from __future__ import annotations

import shutil
from pathlib import Path

__all__ = ["free_bytes"]


def free_bytes(directory: Path) -> int | None:
    """Bytes free on ``directory``'s filesystem, or ``None`` when it cannot be read."""
    try:
        return shutil.disk_usage(directory).free
    except OSError:
        return None
