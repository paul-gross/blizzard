"""The graceful-shutdown worker drain (issue #12) — ``ResumeMarking.on_shutdown``'s step.

SIGINTs every marked lease's process group, waits at most :data:`SHUTDOWN_DRAIN_DEADLINE`
in total (one shared budget, not one per worker), then SIGKILLs any survivor. Makes no
durable write: it only waits for the worker's own envelope to reach the stdout file the
next startup's usage recording reads back."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.loop.process import IProcessProbe, interrupt_owned_process

_log = get_logger("blizzard.runner.loop")

#: The drain's total time budget — one shared deadline across every marked worker.
SHUTDOWN_DRAIN_DEADLINE = 60.0

#: How often the drain re-polls the still-signalled groups between now and the deadline.
_POLL_INTERVAL_SECONDS = 0.5


@dataclass(frozen=True)
class ShutdownDrain:
    """SIGINTs, then waits out (bounded), every marked lease's process group."""

    process: IProcessProbe
    clock: IClock
    sleep: Callable[[float], None]
    deadline_seconds: float = SHUTDOWN_DRAIN_DEADLINE

    def run(self, leases: Sequence[LeaseRecord]) -> None:
        pgids: set[int] = set()
        for lease in leases:
            if self._interrupt(lease):
                assert lease.pgid is not None  # `_interrupt` only signals a recorded group
                pgids.add(lease.pgid)
        if not pgids:
            return
        deadline = self.clock.now().timestamp() + self.deadline_seconds
        exited: set[int] = set()
        while len(exited) < len(pgids) and self.clock.now().timestamp() < deadline:
            exited |= {pgid for pgid in pgids - exited if not self.process.group_alive(pgid)}
            if len(exited) < len(pgids):
                self.sleep(_POLL_INTERVAL_SECONDS)
        survivors = pgids - exited
        for pgid in survivors:
            self.process.kill_group(pgid)
        _log.info(
            "shutdown drain complete",
            signalled=len(pgids),
            exited_on_sigint=len(exited),
            killed=len(survivors),
        )

    def _interrupt(self, lease: LeaseRecord) -> bool:
        """The shared guarded interrupt over this lease's recorded group — ``False`` for a
        lease with nothing recorded to signal (no pgid, or an already-dead leader)."""
        return interrupt_owned_process(
            self.process, pid=lease.pid, process_start_time=lease.process_start_time, pgid=lease.pgid
        )
