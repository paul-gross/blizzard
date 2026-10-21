"""A one-shot, argv-based subprocess seam: argv, stdin, an environment, and a bounded timeout,
for a vendor CLI that reads its request off stdin and exits on EOF (``bzh:pluggable-seams``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.process.child_env import ChildEnv

__all__ = ["IOneShotProcess", "OneShotResult"]


@domain_model
@dataclass(frozen=True)
class OneShotResult:
    """One subprocess run's outcome. ``exit_code`` is ``None`` when the process never produced
    one — a timeout (``timed_out=True``) or a launch failure such as a missing binary
    (``timed_out=False``, the failure text in ``stderr``). Never raises: a hung, missing, or
    failing vendor CLI is a failed outcome, not an exception the caller must catch."""

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


class IOneShotProcess(Protocol):
    """Run one bounded, argv-based child process to completion, capturing its output."""

    def run(
        self, argv: Sequence[str], *, stdin: str, timeout: float, env: ChildEnv, settle_seconds: float = 0.0
    ) -> OneShotResult:
        """Run ``argv`` with ``stdin`` written to its standard input, under a ``timeout`` in
        seconds. ``env`` is the child's whole environment — an allowlist profile with any
        per-launch addition stated, never the caller's own. ``stdin`` is closed
        (EOF) ``settle_seconds`` after it is written, not at once, so a reply still being produced
        is never cut off; 0 closes right away. A timeout kills the child; a launch failure is caught."""
        ...
