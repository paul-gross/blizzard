"""The selftest job resource and its verdicts — pure, no I/O.

:class:`SelfTestRun` is a resource with a result, not an RPC verb. Check names are
module constants rather than free-form strings, so every reader agrees on the same
seven identifiers. Each ``*_verdict`` function judges one check from values the
orchestration in :mod:`.checks` already observed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import ClassVar, Literal

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.selftest_result import LatestSelfTestResult, SelfTestTerminalStatus

SelfTestStatus = Literal["running", SelfTestTerminalStatus]

# The seven adapter-drift checks, in the order a run performs them (the last two were added later).
SPAWN_SESSION_ID = "spawn_session_id"
END_TO_END_EDIT_COMMIT = "end_to_end_edit_commit"
VERDICT_ELICITATION = "verdict_elicitation"
AUTOMATED_RESUME = "automated_resume"
RESUME_COMMAND = "resume_command"
USAGE_PARSING = "usage_parsing"
TRANSCRIPT_READABILITY = "transcript_readability"


class SelfTestTransitionError(RuntimeError):
    """A selftest run was asked for a transition its status does not allow."""


@domain_model
@dataclass(frozen=True)
class SelfTestCheck:
    """One pass/fail check within a selftest run, with a human-readable detail."""

    name: str
    passed: bool
    detail: str


@domain_model
@dataclass(frozen=True)
class SelfTestRun:
    """A selftest job resource: minted ``running``, concluded exactly once (:attr:`TRANSITIONS`) through
    :meth:`conclude`, :meth:`overran`, or :meth:`crashed`; only a terminal run yields the durable
    :meth:`result_record`. Runs are independent: the harness's durable result is whichever is recorded
    last, not whichever was minted last."""

    id: str
    harness: str
    status: SelfTestStatus = "running"
    checks: tuple[SelfTestCheck, ...] = field(default=())
    error: str | None = None

    #: Each status -> the statuses a run may move to from it.
    TRANSITIONS: ClassVar[Mapping[SelfTestStatus, frozenset[SelfTestStatus]]] = {
        "running": frozenset({"passed", "failed"}),
        "passed": frozenset(),
        "failed": frozenset(),
    }

    def conclude(self, checks: Sequence[SelfTestCheck]) -> SelfTestRun:
        """The run with every check resolved: ``passed`` only when at least one check ran and every
        one passed — a run that checked nothing proved nothing."""
        status: SelfTestTerminalStatus = "passed" if checks and all(c.passed for c in checks) else "failed"
        return self._to(status, checks=tuple(checks), error=None)

    def overran(self, budget_seconds: float) -> SelfTestRun:
        """The run abandoned for exceeding its wall-clock budget: failed, with no checks."""
        detail = f"selftest exceeded its {budget_seconds:g}s wall-clock budget — the harness appears hung"
        return self._to("failed", checks=(), error=detail)

    def crashed(self, error: str) -> SelfTestRun:
        """The run whose checks runner itself raised: failed, with the exception's text."""
        return self._to("failed", checks=(), error=error)

    def result_record(self, at: datetime) -> LatestSelfTestResult:
        """The durable fact a concluded run leaves for its harness, recorded at ``at``."""
        if self.status == "running":
            raise SelfTestTransitionError(f"selftest run {self.id} is still running — nothing to record")
        return LatestSelfTestResult(harness_id=self.harness, status=self.status, error=self.error, recorded_at=at)

    def _to(
        self, status: SelfTestTerminalStatus, *, checks: tuple[SelfTestCheck, ...], error: str | None
    ) -> SelfTestRun:
        if status not in self.TRANSITIONS[self.status]:
            raise SelfTestTransitionError(f"selftest run {self.id} is already {self.status}")
        return replace(self, status=status, checks=checks, error=error)


def spawn_identity_verdict(session_id: str, expected_session_id: str, *, honors_hint: bool) -> SelfTestCheck | None:
    """The spawn check's failure when the session id the harness reported is not authoritative —
    empty, or differing from the pre-assigned one where the harness declares it honors that hint —
    else ``None``: the id holds, and the exit wait decides the check."""
    if not session_id:
        return SelfTestCheck(SPAWN_SESSION_ID, False, "spawn returned an empty session id — never authoritative")
    if honors_hint and session_id != expected_session_id:
        detail = f"expected the pre-assigned session id {expected_session_id!r}, got {session_id!r}"
        return SelfTestCheck(SPAWN_SESSION_ID, False, detail)
    return None


def spawn_exit_verdict(pid: int, session_id: str, *, exited: bool, exit_timeout_seconds: float) -> SelfTestCheck:
    """The spawn check once its session id holds: passed only when the worker exited on its own."""
    if not exited:
        detail = f"worker pid {pid} did not exit within {exit_timeout_seconds}s (exit-is-done undetected)"
        return SelfTestCheck(SPAWN_SESSION_ID, False, detail)
    detail = f"spawned pid {pid} honoring session id {session_id!r}; exit-is-done detected"
    return SelfTestCheck(SPAWN_SESSION_ID, True, detail)


def skipped_after_failed_spawn() -> list[SelfTestCheck]:
    """Every check after the spawn gate, failed as skipped: without a spawned handle none can run."""
    skipped = "skipped — the spawn/session-id check failed first"
    return [
        SelfTestCheck(name, False, skipped)
        for name in (
            END_TO_END_EDIT_COMMIT,
            VERDICT_ELICITATION,
            AUTOMATED_RESUME,
            RESUME_COMMAND,
            USAGE_PARSING,
            TRANSCRIPT_READABILITY,
        )
    ]


def commit_verdict(commit_count: int) -> SelfTestCheck:
    """Passed when the scratch repo holds the worker's own edit on top of its baseline commit."""
    if commit_count < 2:
        detail = f"only {commit_count} commit(s) in the scratch repo — no edit landed"
        return SelfTestCheck(END_TO_END_EDIT_COMMIT, False, detail)
    return SelfTestCheck(END_TO_END_EDIT_COMMIT, True, f"{commit_count - 1} new commit(s) landed in the scratch repo")


def judge_verdict(*, exited: bool, choice: str | None) -> SelfTestCheck:
    """Passed when the judgement process exited and its reply parsed to a ``<Choice>``."""
    if not exited:
        return SelfTestCheck(VERDICT_ELICITATION, False, "judgement process never exited")
    if choice is None:
        return SelfTestCheck(VERDICT_ELICITATION, False, "judgement resume produced no parseable <Choice>")
    return SelfTestCheck(VERDICT_ELICITATION, True, f"parsed verdict {choice!r}")


def resume_verdict(pid: int, session_id: str) -> SelfTestCheck:
    """Passed when the automated resume launched a real process (a positive pid)."""
    if pid <= 0:
        return SelfTestCheck(AUTOMATED_RESUME, False, f"resume_with_message returned a non-positive pid ({pid})")
    return SelfTestCheck(AUTOMATED_RESUME, True, f"resumed session {session_id!r} as pid {pid}")


def resume_command_verdict(command: str | None, session_id: str, workdir: str) -> SelfTestCheck:
    """Passed when the operator resume command names both the session and its workdir."""
    if not command or session_id not in command or workdir not in command:
        return SelfTestCheck(RESUME_COMMAND, False, f"resume command missing session/workdir: {command!r}")
    return SelfTestCheck(RESUME_COMMAND, True, command)
