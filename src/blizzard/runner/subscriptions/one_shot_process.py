"""A one-shot, argv-based subprocess seam — distinct from
:class:`~blizzard.runner.loop.checks.ICheckRunner` (an authored shell string, no stdin) and
:class:`~blizzard.runner.harness.process_launch.ProcessLauncher` (a long-lived worker child
with its own process group and parent-death signal). A credential renewer drives a vendor CLI
that reads its request off stdin and exits on EOF — argv, stdin, and a bounded timeout are the
whole shape that needs (``bzh:seam-size-ceiling``, ``bzh:pluggable-seams``)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = ["IOneShotProcess", "OneShotResult"]


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
        self, argv: Sequence[str], *, stdin: str, timeout: float, env: Mapping[str, str], settle_seconds: float = 0.0
    ) -> OneShotResult:
        """Run ``argv`` with ``stdin`` written to its standard input, under a ``timeout``
        in seconds. ``env`` replaces the child's environment entirely — there is no
        "inherit the caller's own" default, so every caller states exactly what a vendor
        CLI needs to see. ``stdin`` is closed (EOF) ``settle_seconds`` after it is fully
        written, not immediately: a child whose reply to an in-flight request is still
        pending can otherwise read EOF as "abandon it" and drop that reply. The default,
        0, closes right away, for a child that only needs a batch of input and its output.
        A timeout kills the child; a launch failure (e.g. the binary is not on ``PATH``)
        is caught; neither raises."""
        ...
