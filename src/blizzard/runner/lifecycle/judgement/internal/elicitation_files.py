"""The filesystem driver behind ``IElicitationFiles``.

Load-bearing, unlike the diagnostic worker-stdout lane (`bzh:daemon-stdout-to-file`): the verdict
and usage live only here, so ``root`` is never the empty-disables string ``WorkerStdoutFiles``
accepts. One file per launch attempt, never appended to, so a relaunch can never corrupt the first."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass

from blizzard.foundation.roles import collaborator
from blizzard.runner.lifecycle.judgement.elicitation_files import IElicitationFiles


@collaborator
@dataclass(frozen=True)
class ElicitationFiles:
    """One runner's elicitation-output file layout, rooted at ``root`` — created once at
    wiring time (``build.py``), same as the sibling ``WorkerStdoutFiles``
    (``leases/internal/worker_stdout_files.py``), so a path-computing
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
        past the durably recorded relaunch count; the bound's reason is
        ``src/blizzard/runner/leases/internal/worker_stdout_files.py``'s ``WorkerStdoutFiles.cleanup``."""
        for attempt in range(0, through_attempt + 2):
            with contextlib.suppress(OSError):
                os.remove(self.output_path(lease_id, epoch, attempt))


def _conforms_elicitation_files(x: ElicitationFiles) -> IElicitationFiles:
    return x
