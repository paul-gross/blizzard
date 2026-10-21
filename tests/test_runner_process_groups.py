"""Process-group signalling in ``src/blizzard/runner/process/groups.py``, against real process groups: the primitive swallows a
vanished group, and the TERM, grace, KILL sequence reaches a member that ignores TERM."""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from blizzard.runner.process.groups import group_exists, signal_group, terminate_group

pytestmark = pytest.mark.unit

_IGNORES_TERM = (
    "import signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); open(sys.argv[1], 'w'); time.sleep(60)"
)


def _spawn(code: str, ready: Path | None = None) -> subprocess.Popen[bytes]:
    """A new-session child running ``code``; given ``ready``, it returns once the child has created that file."""
    argv = [sys.executable, "-c", code, *([str(ready)] if ready is not None else [])]
    process = subprocess.Popen(argv, start_new_session=True)
    while ready is not None and not ready.exists():
        time.sleep(0.01)
    return process


def _waiting(process: subprocess.Popen[bytes]) -> Callable[[float], int | None]:
    """The grace wait the sequence bounds: the exit code, or ``None`` once the grace runs out."""

    def wait(grace: float) -> int | None:
        try:
            return process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            return None

    return wait


def test_signalling_a_group_that_already_exited_is_not_an_error() -> None:
    process = _spawn("pass")
    process.wait()
    signal_group(process.pid, signal.SIGKILL)
    assert not group_exists(process.pid)


def test_signalling_a_live_group_reaches_it() -> None:
    process = _spawn("import time; time.sleep(60)")
    try:
        assert group_exists(process.pid)
        signal_group(process.pid, signal.SIGKILL)
        assert process.wait(timeout=10) == -signal.SIGKILL
    finally:
        process.kill()


def test_the_sequence_kills_a_leader_that_ignores_term_once_the_grace_runs_out(tmp_path: Path) -> None:
    process = _spawn(_IGNORES_TERM, tmp_path / "ready")
    try:
        assert terminate_group(process.pid, grace=0.3, wait=_waiting(process)) is None
        assert process.wait(timeout=10) == -signal.SIGKILL
    finally:
        process.kill()


def test_the_sequence_returns_what_the_wait_returned_when_the_leader_exits_on_term() -> None:
    process = _spawn("import time; time.sleep(60)")
    try:
        assert terminate_group(process.pid, grace=10, wait=_waiting(process)) == -signal.SIGTERM
    finally:
        process.kill()
