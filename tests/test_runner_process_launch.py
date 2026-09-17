"""The runner-owned process launcher (``ProcessLauncher``, D4): process-group ownership
and the parent-death signal's thread scoping. ``PR_SET_PDEATHSIG`` tracks the death of the
SPECIFIC OS thread that called it, not the process — a launcher forking directly from a
tick-scoped thread would SIGKILL every worker the instant that thread exits, even
mid-``driver.stop()``; ``ProcessLauncher`` proxies fork/exec through its own executor to avoid this."""

from __future__ import annotations

import contextlib
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from blizzard.runner.loop.process import LinuxProcessProbe
from blizzard.runner.loop.process_launch import ProcessLauncher


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.unit
def test_launched_process_gets_its_own_process_group() -> None:
    launcher = ProcessLauncher(LinuxProcessProbe())
    launched = launcher.launch(["sleep", "5"], cwd=None, env=dict(os.environ), stdout=None, stderr=None)
    try:
        assert launched.pgid == launched.pid
    finally:
        os.killpg(launched.pgid, signal.SIGKILL)
        _reap(launched.pid)


@pytest.mark.unit
def test_stopping_the_launching_thread_does_not_kill_an_already_launched_child() -> None:
    """Join the launching executor (simulating ``driver.stop()`` of the tick thread) while
    the test process (standing in for the daemon) keeps running; a bare ``preexec_fn`` on
    the calling thread would have SIGKILLed the child right there instead."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-spawner")
    launcher = ProcessLauncher(LinuxProcessProbe(), executor=executor)
    launched = launcher.launch(["sleep", "5"], cwd=None, env=dict(os.environ), stdout=None, stderr=None)
    try:
        # Joins the executor's one worker thread — the exact thread that called fork()/exec()
        # for `launched` above — without touching this test process's own main thread at all.
        executor.shutdown(wait=True)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            time.sleep(0.02)
        assert _is_alive(launched.pid), "the launching thread's own death killed a healthy child"
    finally:
        os.killpg(launched.pgid, signal.SIGKILL)
        _reap(launched.pid)


def _reap(pid: int) -> None:
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, 0)
