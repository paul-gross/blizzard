"""The runner-owned process launcher (``ProcessLauncher``, D4): process-group ownership
and the parent-death signal's thread scoping. ``PR_SET_PDEATHSIG`` tracks the death of the
SPECIFIC OS thread that called it, not the process — a launcher forking directly from a
tick-scoped thread would SIGKILL every worker the instant that thread exits, even
mid-``driver.stop()``; ``ProcessLauncher`` proxies fork/exec through its own executor to avoid this."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Any

import pytest

from blizzard.runner.loop.process import LinuxProcessProbe
from blizzard.runner.loop.process_launch import LaunchedProcess, ProcessLauncher


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _launch(launcher: ProcessLauncher) -> LaunchedProcess:
    return launcher.launch(["sleep", "5"], cwd=None, env=dict(os.environ), stdout=None, stderr=None)


@pytest.mark.unit
def test_launched_process_gets_its_own_process_group() -> None:
    launcher = ProcessLauncher(LinuxProcessProbe())
    launched = _launch(launcher)
    try:
        assert launched.pgid == launched.pid
    finally:
        os.killpg(launched.pgid, signal.SIGKILL)
        _reap(launched.pid)


@pytest.mark.unit
@pytest.mark.parametrize("prefix", ["blizzard-spawner", "test-spawner"])
def test_launch_forks_off_the_calling_thread_onto_a_persistent_one(
    monkeypatch: pytest.MonkeyPatch, prefix: str
) -> None:
    """The structural property the parent-death signal's thread scoping demands, and the
    one holding on any kernel: the fork never runs on the caller's own (tick-scoped)
    thread, but on a persistent spawner — the module's shared one, or an injected one."""
    executor: Executor | None = None
    if prefix != "blizzard-spawner":
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=prefix)
    seen: list[threading.Thread] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        seen.append(threading.current_thread())
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    launched = _launch(ProcessLauncher(LinuxProcessProbe(), executor=executor))
    try:
        (forker,) = seen
        assert forker is not threading.current_thread()
        assert forker.name.startswith(prefix)
        assert forker.is_alive()
    finally:
        os.killpg(launched.pgid, signal.SIGKILL)
        _reap(launched.pid)
        if executor is not None:
            executor.shutdown(wait=True)


@pytest.mark.unit
def test_a_worker_outlives_the_short_lived_thread_that_requested_its_launch() -> None:
    """The production scenario: the tick thread requests a launch, then exits on a graceful
    ``driver.stop()`` while the daemon runs on — its healthy worker must still be alive."""
    launcher = ProcessLauncher(LinuxProcessProbe())
    launched: list[LaunchedProcess] = []
    requester = threading.Thread(target=lambda: launched.append(_launch(launcher)), name="tick")
    requester.start()
    requester.join()

    (worker,) = launched
    try:
        time.sleep(0.5)
        assert _is_alive(worker.pid), "the requesting thread's own death killed a healthy worker"
    finally:
        os.killpg(worker.pgid, signal.SIGKILL)
        _reap(worker.pid)


def _reap(pid: int) -> None:
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, 0)
