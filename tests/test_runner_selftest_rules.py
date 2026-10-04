"""The selftest rules pinned by value: a run's terminal status, its legal transitions, the durable
record it leaves, and each check's verdict over already-observed values."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.harness.selftest_result import LatestSelfTestResult
from blizzard.runner.selftest.model import (
    AUTOMATED_RESUME,
    END_TO_END_EDIT_COMMIT,
    RESUME_COMMAND,
    SPAWN_SESSION_ID,
    TRANSCRIPT_READABILITY,
    USAGE_PARSING,
    VERDICT_ELICITATION,
    SelfTestCheck,
    SelfTestRun,
    SelfTestTransitionError,
    commit_verdict,
    judge_verdict,
    resume_command_verdict,
    resume_verdict,
    skipped_after_failed_spawn,
    spawn_exit_verdict,
    spawn_identity_verdict,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_PASS = SelfTestCheck("a", True, "ok")
_FAIL = SelfTestCheck("b", False, "no")


def _run() -> SelfTestRun:
    return SelfTestRun(id="st_1", harness="claude_code")


def test_conclude_overran_crashed() -> None:
    assert _run().conclude([_PASS, _PASS]).status == "passed"
    concluded = _run().conclude([_PASS, _FAIL])
    assert (concluded.status, concluded.checks, concluded.error) == ("failed", (_PASS, _FAIL), None)
    overran = _run().overran(0.2)
    assert (overran.status, overran.checks) == ("failed", ())
    assert overran.error == "selftest exceeded its 0.2s wall-clock budget — the harness appears hung"
    crashed = _run().crashed("boom")
    assert (crashed.status, crashed.checks, crashed.error) == ("failed", (), "boom")


def test_zero_checks_fail() -> None:
    assert _run().conclude([]).status == "failed"


def test_terminal_run_refuses_conclude() -> None:
    concluded = _run().conclude([_PASS])
    with pytest.raises(SelfTestTransitionError):
        concluded.conclude([_PASS])
    with pytest.raises(SelfTestTransitionError):
        concluded.overran(1.0)
    with pytest.raises(SelfTestTransitionError):
        concluded.crashed("late")
    assert SelfTestRun.TRANSITIONS["running"] == frozenset({"passed", "failed"})
    assert SelfTestRun.TRANSITIONS["passed"] == frozenset()


def test_result_record_carries_terminal_status() -> None:
    assert _run().crashed("boom").result_record(_AT) == LatestSelfTestResult(
        harness_id="claude_code", status="failed", error="boom", recorded_at=_AT
    )
    assert _run().conclude([_PASS]).result_record(_AT).status == "passed"


def test_result_record_refuses_running() -> None:
    with pytest.raises(SelfTestTransitionError):
        _run().result_record(_AT)


def test_concurrent_runs_are_independent() -> None:
    first = SelfTestRun(id="st_1", harness="claude_code")
    second = SelfTestRun(id="st_2", harness="claude_code")
    assert first.conclude([_PASS]).status == "passed"
    assert second.status == "running"
    assert second.crashed("boom").result_record(_AT).status == "failed"


def test_check_verdicts_spawn() -> None:
    empty = spawn_identity_verdict("", "selftest-x", honors_hint=True)
    assert empty == SelfTestCheck(SPAWN_SESSION_ID, False, "spawn returned an empty session id — never authoritative")
    mismatch = spawn_identity_verdict("other", "selftest-x", honors_hint=True)
    assert mismatch == SelfTestCheck(
        SPAWN_SESSION_ID, False, "expected the pre-assigned session id 'selftest-x', got 'other'"
    )
    assert spawn_identity_verdict("other", "selftest-x", honors_hint=False) is None
    assert spawn_exit_verdict(7, "s", exited=False, exit_timeout_seconds=30.0) == SelfTestCheck(
        SPAWN_SESSION_ID, False, "worker pid 7 did not exit within 30.0s (exit-is-done undetected)"
    )
    assert spawn_exit_verdict(7, "s", exited=True, exit_timeout_seconds=30.0) == SelfTestCheck(
        SPAWN_SESSION_ID, True, "spawned pid 7 honoring session id 's'; exit-is-done detected"
    )


def test_check_verdicts_skipped_after_failed_spawn() -> None:
    skipped = skipped_after_failed_spawn()
    assert [check.name for check in skipped] == [
        END_TO_END_EDIT_COMMIT,
        VERDICT_ELICITATION,
        AUTOMATED_RESUME,
        RESUME_COMMAND,
        USAGE_PARSING,
        TRANSCRIPT_READABILITY,
    ]
    assert all(not c.passed and c.detail == "skipped — the spawn/session-id check failed first" for c in skipped)


def test_check_verdicts_commit() -> None:
    assert commit_verdict(1) == SelfTestCheck(
        END_TO_END_EDIT_COMMIT, False, "only 1 commit(s) in the scratch repo — no edit landed"
    )
    assert commit_verdict(3) == SelfTestCheck(
        END_TO_END_EDIT_COMMIT, True, "2 new commit(s) landed in the scratch repo"
    )


def test_check_verdicts_judge() -> None:
    assert judge_verdict(exited=False, choice="pass") == SelfTestCheck(
        VERDICT_ELICITATION, False, "judgement process never exited"
    )
    assert judge_verdict(exited=True, choice=None) == SelfTestCheck(
        VERDICT_ELICITATION, False, "judgement resume produced no parseable <Choice>"
    )
    assert judge_verdict(exited=True, choice="pass") == SelfTestCheck(
        VERDICT_ELICITATION, True, "parsed verdict 'pass'"
    )


def test_check_verdicts_resume() -> None:
    assert resume_verdict(0, "s") == SelfTestCheck(
        AUTOMATED_RESUME, False, "resume_with_message returned a non-positive pid (0)"
    )
    assert resume_verdict(9, "s") == SelfTestCheck(AUTOMATED_RESUME, True, "resumed session 's' as pid 9")


def test_check_verdicts_resume_command() -> None:
    assert resume_command_verdict("claude --resume s", "s", "/w") == SelfTestCheck(
        RESUME_COMMAND, False, "resume command missing session/workdir: 'claude --resume s'"
    )
    assert resume_command_verdict("", "s", "/w").passed is False
    assert resume_command_verdict("cd /w && claude --resume s", "s", "/w") == SelfTestCheck(
        RESUME_COMMAND, True, "cd /w && claude --resume s"
    )
