"""Subprocess adapter for the check-runner seam (package-private) — the reference
:class:`~blizzard.runner.loop.checks.ICheckRunner` binding, running a node's authored ``checks:``
command in a leased worktree under a timeout and capturing a bounded output tail (issue #114).

The child environment is built from the worker-env allowlist (``bzh:worker-env-allowlist``): a check
runs arbitrary repo tooling, so a daemon credential must be absent by construction, not filtered."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.loop.checks import CheckOutcome, ICheckRunner

_log = get_logger("blizzard.runner.checks")

# The captured tail's ceiling — the last N characters of the check's combined stdout+stderr.
# The tail is evidence, not the full log.
_TAIL_MAX_CHARS = 4000


@dataclass(frozen=True)
class CheckOutput:
    """A check's combined stdout+stderr, plus whatever ``note`` the caller appends to it."""

    text: str

    @classmethod
    def of(cls, stdout: bytes | str | None, stderr: bytes | str | None, *, note: str = "") -> CheckOutput:
        return cls(cls._decoded(stdout) + cls._decoded(stderr) + note)

    @property
    def tail(self) -> str:
        """The last :data:`_TAIL_MAX_CHARS` characters, prefixed with an elision marker when
        truncated so a reader sees the output was clipped."""
        if len(self.text) <= _TAIL_MAX_CHARS:
            return self.text
        return "…(output truncated)…\n" + self.text[-_TAIL_MAX_CHARS:]

    @staticmethod
    def _decoded(stream: bytes | str | None) -> str:
        """``subprocess.TimeoutExpired`` carries the partial output as ``bytes`` (or ``None``)
        even under ``text=True``; normalize either to ``str``."""
        if stream is None:
            return ""
        if isinstance(stream, bytes):
            return stream.decode("utf-8", errors="replace")
        return stream


class SubprocessCheckRunner:
    """Run a node's ``checks:`` command in a leased worktree, via the shell."""

    def __init__(self, *, env_passthrough: Sequence[str] = ()) -> None:
        # The operator's declared worker-env passthrough — the same widening the harness children
        # get, so a check needing an operator-declared var behaves like the worker did.
        self._env_passthrough = tuple(env_passthrough)

    def run(self, command: str, cwd: str, timeout: int) -> CheckOutcome:
        env = AllowlistedEnv.of(self._env_passthrough).variables
        try:
            result = subprocess.run(
                command,
                shell=True,  # a check is an authored shell string (`mise run lint`), not an argv
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # A timeout is a red check, never a raise — a hung check cannot wedge the tick.
            captured = CheckOutput.of(exc.stdout, exc.stderr, note=f"\n[timed out after {timeout}s]")
            _log.warning("check timed out", command=command, cwd=cwd, timeout=timeout)
            return CheckOutcome(passed=False, output_tail=captured.tail)
        passed = result.returncode == 0
        if not passed:
            _log.info("check failed", command=command, cwd=cwd, returncode=result.returncode)
        return CheckOutcome(passed=passed, output_tail=CheckOutput.of(result.stdout, result.stderr).tail)


def _conforms_check_runner(x: SubprocessCheckRunner) -> ICheckRunner:
    return x
