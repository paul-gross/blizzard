"""The subprocess check-runner adapter (unit tier, issue #114).

Real subprocesses prove the reference ``ICheckRunner`` binding: exit 0 is a pass,
non-zero is a red check, a timeout is a red check (never a raise), output is captured
as a bounded tail, and the child env is built from the worker-env allowlist."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.loop.internal.subprocess_check_runner import SubprocessCheckRunner

pytestmark = pytest.mark.unit


def test_a_zero_exit_is_a_pass_and_captures_output() -> None:
    outcome = SubprocessCheckRunner().run("echo hello", cwd=".", timeout=30)
    assert outcome.passed is True
    assert "hello" in outcome.output_tail


def test_a_nonzero_exit_is_a_red_check() -> None:
    outcome = SubprocessCheckRunner().run("echo boom >&2; exit 3", cwd=".", timeout=30)
    assert outcome.passed is False
    assert "boom" in outcome.output_tail  # stderr is captured in the tail too


def test_a_timeout_is_a_red_check_not_a_raise() -> None:
    outcome = SubprocessCheckRunner().run("sleep 5", cwd=".", timeout=1)
    assert outcome.passed is False
    assert "timed out" in outcome.output_tail


def test_runs_in_the_given_cwd(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("x")
    outcome = SubprocessCheckRunner().run("ls marker.txt", cwd=str(tmp_path), timeout=30)
    assert outcome.passed is True
    assert "marker.txt" in outcome.output_tail


def test_the_child_env_excludes_daemon_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon credential in the runner's own environment must not reach a check
    subprocess (``bzh:worker-env-allowlist``) — the allowlist omits it by construction."""
    monkeypatch.setenv("BZ_HUB_TOKEN", "super-secret")
    outcome = SubprocessCheckRunner().run("echo token=[${BZ_HUB_TOKEN:-absent}]", cwd=".", timeout=30)
    assert outcome.passed is True
    assert "token=[absent]" in outcome.output_tail


def test_an_operator_passthrough_var_reaches_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOOLCHAIN_QUIRK", "on")
    runner = SubprocessCheckRunner(env_passthrough=("MY_TOOLCHAIN_QUIRK",))
    outcome = runner.run("echo quirk=[${MY_TOOLCHAIN_QUIRK:-absent}]", cwd=".", timeout=30)
    assert "quirk=[on]" in outcome.output_tail


def test_the_output_tail_is_bounded() -> None:
    # Emit far more than the tail ceiling; the tail is clipped with an elision marker.
    outcome = SubprocessCheckRunner().run("for i in $(seq 1 5000); do echo linexxxxxx$i; done", cwd=".", timeout=30)
    assert outcome.passed is True
    assert len(outcome.output_tail) < 5000
    assert "output truncated" in outcome.output_tail
    # The last line survives (it is the tail), an early one does not.
    assert "linexxxxxx5000" in outcome.output_tail
    assert "linexxxxxx1\n" not in outcome.output_tail
