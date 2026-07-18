"""The filesystem-backed per-chunk hub workdir (``bzh:pluggable-seams``).

Confined to ``internal/`` (adapter placement, ``bzh:dependency-inversion``): all
``pathlib``/``shutil`` usage for the hub workdir lives here; the domain sees only
:class:`~blizzard.hub.delivery.workdir.IHubWorkdir`.

This adapter owns the **folder**, not its contents: it creates and reclaims a bare
directory per chunk under the hub runtime dir. Whether that directory is empty (a
first visit) or holds one or more warm git clones (a later visit) is up to the
declared ``run:`` commands themselves — a step's own ``git clone``/``git fetch``,
never `--depth` (spike #68 finding 4: a shallow clone refuses an "unrelated
histories" merge). Losing the folder loses time, never correctness: a command
tolerates an empty/missing folder by cloning fresh.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from blizzard.hub.delivery.workdir import IHubWorkdir


class FilesystemHubWorkdir:
    """Per-chunk workdir folders under ``root`` — one subdirectory per chunk id."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def ensure(self, chunk_id: str) -> str:
        path = self._root / chunk_id
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def expire(self, chunk_id: str) -> None:
        shutil.rmtree(self._root / chunk_id, ignore_errors=True)

    def list_orphans(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return [p.name for p in self._root.iterdir() if p.is_dir()]


def _conforms_hub_workdir(x: FilesystemHubWorkdir) -> IHubWorkdir:
    return x
