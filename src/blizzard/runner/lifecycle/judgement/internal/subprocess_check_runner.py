"""Subprocess adapter for the check-runner seam (package-private) — the reference
:class:`~blizzard.runner.lifecycle.judgement.check_runner.ICheckRunner` binding, running a node's authored ``checks:``
command in a leased worktree under a timeout and capturing a bounded output tail.

The child environment is the allowlist's tool profile (``bzh:worker-env-allowlist``): a check runs arbitrary repo
tooling, so a daemon credential must be absent by construction, not filtered. The check leads its own group through the
launch seam, so a timeout kills every descendant it forked, not just the shell."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.runner.lifecycle.judgement.check_runner import CheckOutcome, ICheckRunner
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses, ShellCommand

_log = get_logger("blizzard.runner.checks")

# The captured tail's ceiling — the last N characters of the check's combined stdout+stderr.
# The tail is evidence, not the full log.
_TAIL_MAX_CHARS = 4000


@domain_model
@dataclass(frozen=True)
class CheckOutput:
    """A check's combined stdout+stderr, plus whatever ``note`` the caller appends to it."""

    text: str

    @classmethod
    def of(cls, stdout: str, stderr: str, *, note: str = "") -> CheckOutput:
        return cls(stdout + stderr + note)

    @property
    def tail(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The last :data:`_TAIL_MAX_CHARS` characters, prefixed with an elision marker when
        truncated so a reader sees the output was clipped."""
        if len(self.text) <= _TAIL_MAX_CHARS:
            return self.text
        return "…(output truncated)…\n" + self.text[-_TAIL_MAX_CHARS:]


class SubprocessCheckRunner:
    """Run a node's ``checks:`` command in a leased worktree, via the shell."""

    def __init__(self, *, children: IChildProcesses, env: ChildEnv) -> None:
        self._children = children
        self._env = env

    def run(self, command: str, cwd: str, timeout: int) -> CheckOutcome:
        # A check is an authored shell string (`mise run lint`), not an argv.
        result = self._children.run(ShellCommand(command), cwd=cwd, env=self._env, timeout=timeout)
        if result.timed_out:
            # A timeout is a red check, never a raise — a hung check cannot wedge the tick.
            captured = CheckOutput.of(result.stdout, result.stderr, note=f"\n[timed out after {timeout}s]")
            _log.warning("check timed out", command=command, cwd=cwd, timeout=timeout)
            return CheckOutcome(passed=False, output_tail=captured.tail)
        passed = result.exit_code == 0
        if not passed:
            _log.info("check failed", command=command, cwd=cwd, returncode=result.exit_code)
        return CheckOutcome(passed=passed, output_tail=CheckOutput.of(result.stdout, result.stderr).tail)


def _conforms_check_runner(x: SubprocessCheckRunner) -> ICheckRunner:
    return x
