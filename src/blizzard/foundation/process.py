"""Low-level process start-time reading — the reuse-proof half of pid liveness.

The OS reuses pids, so a recorded pid may later name a different process; pairing it
with the process **start time** pins the identity. Linux ``/proc`` is the reference
source; the value is an opaque stable token compared for equality, never interpreted."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcStat:
    """One process's ``/proc/<pid>/stat`` fields after ``comm`` — ``state`` at 0, ``starttime`` at 19.

    Empty for a pid naming no process, or whose stat line is unreadable or malformed."""

    fields: tuple[str, ...]

    @classmethod
    def of(cls, pid: int) -> ProcStat:
        """Read ``pid``'s stat line — a vanished or unreadable pid yields empty fields."""
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                stat = fh.read()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return cls(())
        # ``comm`` (field 2) is paren-wrapped and may contain spaces and parens, so split
        # after the *last* ``)``; the remainder begins at field 3 (``state``).
        close = stat.rfind(")")
        return cls(tuple(stat[close + 1 :].split())) if close != -1 else cls(())

    @property
    def start_time(self) -> str | None:
        """The stable start-time token, or ``None`` when this pid named no live process."""
        return self.fields[19] if len(self.fields) >= 20 else None

    @property
    def zombie(self) -> bool:
        """True iff the kernel marks the process defunct — exited, not yet reaped.

        Such a process keeps its ``/proc`` entry and start time, so a bare start-time
        match reads it as alive forever."""
        return len(self.fields) >= 1 and self.fields[0] == "Z"
