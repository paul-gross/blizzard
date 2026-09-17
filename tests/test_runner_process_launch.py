"""The runner-owned process launcher (``ProcessLauncher``, D4) — process-group ownership
and the parent-death signal's thread scoping.

``PR_SET_PDEATHSIG`` (``man 2 prctl``) tracks the death of the SPECIFIC OS thread that
called it, not the process it belongs to. A launcher that forks directly from a
request-scoped or tick-scoped thread would SIGKILL every already-launched, healthy
worker the instant that ONE thread exits — even while the daemon process carries on —
silently defeating the orphan-reattach recovery a graceful ``driver.stop()`` promises
(``docs/deployment/recovery.md``). ``ProcessLauncher`` proxies the actual fork/exec
through its own long-lived executor for exactly this reason; these tests hold it to that.
"""

from __future__ import annotations

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
    """The regression this cluster's review found missing: join the SPECIFIC thread/executor
    that issued the launch — simulating a graceful ``driver.stop()`` of the reconciliation
    loop's own tick thread — while the test process (standing in for the daemon process)
    keeps right on running, and assert the child is still alive afterward.

    Under the pre-fix code (``preexec_fn`` calling ``prctl`` directly on whatever thread
    called ``Popen``), shutting down this single-worker executor reproduced exactly the
    thread-death `driver.stop()` triggers in production, and the child was SIGKILLed the
    moment the executor's worker thread exited — well before the daemon process itself did."""
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
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
