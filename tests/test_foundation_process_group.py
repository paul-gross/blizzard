"""Process-group signalling in ``foundation/process.py``, against real process groups: the primitive swallows a
vanished group, and the TERM, grace, KILL sequence reaches a member that ignores TERM."""

from __future__ import annotations

import signal
import subprocess
import sys

import pytest

from blizzard.foundation.process import group_exists, signal_group, terminate_group

pytestmark = pytest.mark.unit

_IGNORES_TERM = (
    "import signal, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(1, flush=True); time.sleep(60)"
)


def _spawn(code: str) -> subprocess.Popen[bytes]:
    process = subprocess.Popen([sys.executable, "-c", code], start_new_session=True, stdout=subprocess.PIPE)
    if "print(1" in code:
        assert process.stdout is not None
        process.stdout.readline()
    return process


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


def test_the_sequence_kills_a_leader_that_ignores_term_once_the_grace_runs_out() -> None:
    process = _spawn(_IGNORES_TERM)
    try:
        assert terminate_group(process.pid, grace=0.3, wait=lambda grace: process.wait(timeout=grace)) is None
        assert process.wait(timeout=10) == -signal.SIGKILL
    finally:
        process.kill()


def test_the_sequence_returns_what_the_wait_returned_when_the_leader_exits_on_term() -> None:
    process = _spawn("import time; time.sleep(60)")
    try:
        assert terminate_group(process.pid, grace=10, wait=lambda grace: process.wait(timeout=grace)) == -signal.SIGTERM
    finally:
        process.kill()
