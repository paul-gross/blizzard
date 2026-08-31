"""Where a detached judgement elicitation's reply lands (blizzard#443, D4).

Load-bearing, unlike the diagnostic worker-stdout lane (`bzh:daemon-stdout-to-file`): the
verdict and the attempt's usage live only here, so a launch with nowhere to write fails
loudly rather than proceeding uncollectable — ``root`` is never the empty-disables string
``WorkerStdoutFiles`` accepts. One file per launch attempt, never appended to, so a
relaunch's second document can never corrupt the first's (D4)."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ElicitationFiles:
    """One runner's elicitation-output file layout, rooted at ``root`` — created once at
    wiring time (``build.py``), same as the sibling ``WorkerStdoutFiles``, so a path-computing
    accessor stays pure rather than touching the filesystem on every call."""

    root: str

    def output_path(self, lease_id: str, epoch: int, attempt: int) -> str:
        """This launch attempt's own output file — never shared with another attempt."""
        return os.path.join(self.root, f"{lease_id}.{epoch}.{attempt}.elicitation")

    def read(self, path: str) -> str:
        """The collected reply, or ``""`` when the file is absent/unreadable — the ordinary
        shape of "the process has not written its result yet"."""
        try:
            with open(path, "rb") as f:
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def cleanup(self, lease_id: str, epoch: int, through_attempt: int) -> None:
        """Remove every attempt's output file for this ``(lease_id, epoch)``, bounded one
        past the durably recorded relaunch count for the same un-armable-gap reason
        :meth:`~blizzard.runner.loop.worker_stdout.WorkerStdoutFiles.cleanup` is bounded."""
        for attempt in range(0, through_attempt + 2):
            with contextlib.suppress(OSError):
                os.remove(self.output_path(lease_id, epoch, attempt))
