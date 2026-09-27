"""The per-lease scratch directory a worker session stages drafts, notes, and pulled
assets in — `BLIZZARD_TMPDIR`. Patterned off `WorkerStdoutFiles`: one runner's own
layout, rooted at `root` (`""` disables it), keyed on lease id alone — the directory is
lease-lifetime, not per-generation."""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerScratchDirs:
    """One runner's per-lease scratch-directory layout, rooted at ``root`` (``""`` disables it)."""

    root: str

    def path(self, lease_id: str) -> str:
        """This lease's scratch directory, whether or not it exists yet — or ``""`` when disabled."""
        if not self.root:
            return ""
        return os.path.join(self.root, lease_id)

    def ensure(self, lease_id: str) -> str:
        """Create this lease's directory, owner-only, if it does not already exist; return its
        path (``""`` when disabled). Idempotent — safe on every spawn, resume, and judge of the
        same lease, and recreates a directory lost to anything short of lease closure."""
        path = self.path(lease_id)
        if not path:
            return ""
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)  # `makedirs`'s own `mode` is subject to umask; this is not
        return path

    def remove(self, lease_id: str) -> None:
        """Best-effort recursive removal of this lease's directory; never raises."""
        path = self.path(lease_id)
        if not path:
            return
        with contextlib.suppress(OSError):
            shutil.rmtree(path)

    def sweep_orphans(self, active_lease_ids: Iterable[str]) -> int:
        """Remove every entry under ``root`` whose name is not in ``active_lease_ids`` — the
        one-shot crash reconciliation run at daemon start, before any spawn can race it. Returns
        the count removed; an entry that vanishes mid-sweep is a no-op, not a fault."""
        if not self.root:
            return 0
        active = set(active_lease_ids)
        removed = 0
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            return 0
        for entry in entries:
            if entry.name in active:
                continue
            with contextlib.suppress(OSError):
                shutil.rmtree(entry.path)
                removed += 1
        return removed
