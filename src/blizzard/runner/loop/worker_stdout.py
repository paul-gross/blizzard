"""The per-(lease, generation) files a spawned worker's stdout and stderr redirect to."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.runner.domain.leases import IReadLeaseLivenessRepository, LeaseRecord


@dataclass(frozen=True)
class WorkerStdoutFiles:
    """One runner's worker-output file layout, rooted at ``root`` (``""`` disables it)."""

    root: str
    leases: IReadLeaseLivenessRepository

    def stdout_path(self, lease_id: str, generation: int) -> str:
        """This lease's per-generation stdout redirect target, or ``""`` for no redirect.

        Scoped to ``(lease_id, generation)`` so a readback sees only that attempt's own line,
        and opened in append mode so a retry reusing the generation number does not collide."""
        return self._path(lease_id, generation, "stdout")

    def stderr_path(self, lease_id: str, generation: int) -> str:
        """This lease's per-generation harness-**stderr** redirect target (issue #125), or
        ``""`` — so a launched worker that crashed to stderr leaves a readable tail for the
        ``worker-lost`` event."""
        return self._path(lease_id, generation, "stderr")

    def read_stdout(self, lease_id: str, generation: int) -> str:
        """That generation's captured stdout, or ``""`` when absent/unreadable."""
        return self._read(self.stdout_path(lease_id, generation))

    def stderr_tail(self, lease: LeaseRecord, *, limit: int = 2000) -> str:
        """The tail of this lease's most-recent captured spawn-stderr, or ``""``.

        Best-effort and never raises (a hung-but-live worker that never crashed to stderr, or
        an unconfigured ``root``, is the ordinary empty case) — folded into a failed attempt's
        event detail so a dead worker's last words reach the operator."""
        generation = self.leases.lease_generation(lease.lease_id)
        if generation <= 0:
            return ""
        text = self._read(self.stderr_path(lease.lease_id, generation))
        return text[-limit:] if text else ""

    def sweep(self, *, now: datetime, retention: timedelta) -> int:
        """Remove every captured stdout/stderr file older than ``retention``, judged by its
        own mtime — the periodic prune that bounds ``root``'s growth now that a lease's files
        outlive its release (issue #58). Scans the whole directory rather than any one lease's
        files, so it reaches generations whose owning lease is long gone. Returns the count
        removed; a file that vanishes mid-sweep is a no-op, not a fault."""
        if not self.root:
            return 0
        cutoff = (now - retention).timestamp()
        removed = 0
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            return 0
        for entry in entries:
            if not (entry.name.endswith(".stdout") or entry.name.endswith(".stderr")):
                continue
            with contextlib.suppress(OSError):
                if entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
                    removed += 1
        return removed

    def _path(self, lease_id: str, generation: int, stream: str) -> str:
        if not self.root:
            return ""
        return os.path.join(self.root, f"{lease_id}.{generation}.{stream}")

    def _read(self, path: str) -> str:
        """Never raises: a missing file (nothing redirected, or already cleaned up at release)
        is the ordinary "no envelope" case the caller falls back from, not a fault to log."""
        if not path:
            return ""
        try:
            with open(path, "rb") as f:
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
