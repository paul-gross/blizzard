"""The per-(lease, generation) files a spawned worker's stdout and stderr redirect to."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass

from blizzard.runner.domain.leases import IReadLeaseRepository, LeaseRecord


@dataclass(frozen=True)
class WorkerStdoutFiles:
    """One runner's worker-output file layout, rooted at ``root`` (``""`` disables it)."""

    root: str
    leases: IReadLeaseRepository

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

    def cleanup(self, lease_id: str) -> None:
        """Remove every one of a lease's per-generation stdout files, if any.

        Bounded to the durably recorded generation count plus one: the un-armable spawn-record
        gap can leave a file for a generation whose own ``record_spawn`` never landed. A missing
        file at any of those generations is a no-op."""
        if not self.root:
            return
        for generation in range(1, self.leases.lease_generation(lease_id) + 2):
            with contextlib.suppress(OSError):
                os.remove(self.stdout_path(lease_id, generation))

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
