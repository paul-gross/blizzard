"""Where a detached judgement elicitation's reply lands — the seams over its output files.

The driver is ``internal/elicitation_files.py``."""

from __future__ import annotations

from typing import Protocol


class IReadElicitationReply(Protocol):
    """The read half: a launched elicitation's collected reply."""

    def read(self, path: str) -> str:
        """The collected reply, or ``""`` when the file is absent/unreadable — the ordinary
        shape of "the process has not written its result yet"."""
        ...


class IElicitationFiles(IReadElicitationReply, Protocol):
    """One runner's elicitation-output file layout: where a launch writes, and its cleanup."""

    def output_path(self, lease_id: str, epoch: int, attempt: int) -> str:
        """This launch attempt's own output file — never shared with another attempt."""
        ...

    def cleanup(self, lease_id: str, epoch: int, through_attempt: int) -> None:
        """Remove every attempt's output file for this ``(lease_id, epoch)``, bounded one
        past the durably recorded relaunch count."""
        ...
