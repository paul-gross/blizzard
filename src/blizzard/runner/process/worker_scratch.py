"""The seam over the per-lease scratch directory a worker session stages drafts, notes, and
pulled assets in — `BLIZZARD_TMPDIR`. The driver is ``internal/worker_scratch_dirs.py``."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class IWorkerScratchDirs(Protocol):
    """One runner's per-lease scratch-directory layout (``""`` paths mean it is disabled)."""

    def path(self, lease_id: str) -> str:
        """This lease's scratch directory, whether or not it exists yet — or ``""`` when disabled."""
        ...

    def ensure(self, lease_id: str) -> str:
        """Create this lease's directory, owner-only, if it does not already exist; return its
        path (``""`` when disabled). Idempotent — safe on every spawn, resume, and judge of the
        same lease, and recreates a directory lost to anything short of lease closure."""
        ...

    def remove(self, lease_id: str) -> None:
        """Best-effort recursive removal of this lease's directory; never raises."""
        ...

    def sweep_orphans(self, active_lease_ids: Iterable[str]) -> int:
        """Remove every entry whose name is not in ``active_lease_ids`` — the one-shot crash
        reconciliation run at daemon start, before any spawn can race it. Returns the count
        removed; an entry that vanishes mid-sweep is a no-op, not a fault."""
        ...
