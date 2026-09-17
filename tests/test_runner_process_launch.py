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
import sys
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


# A real child PROCESS, not a thread, performs the deferred launch and then exits outright.
_GRANDCHILD_LAUNCH_SCRIPT = """
import sys
sys.path.insert(0, {src!r})
from blizzard.runner.loop.process import LinuxProcessProbe
from blizzard.runner.loop.process_launch import ProcessLauncher
import os, time

launcher = ProcessLauncher(LinuxProcessProbe())
launched = launcher.launch(
    ["sleep", "30"], cwd=None, env=dict(os.environ), stdout=None, stderr=None, defer_disarm=True
)
with open(sys.argv[1], "w") as f:
    f.write(f"{{launched.pid}} {{launched.pgid}}")
if len(sys.argv) > 2 and sys.argv[2] == "confirm":
    launched.confirm_durable()
    time.sleep(0.2)  # let the trampoline's own read/disarm/exec actually run
# Exits here — this script's own death IS the parent-death event, standing in for either a
# daemon crash or its ordinary graceful exit: the OS cannot tell the two apart, and this
# test does not try to.
"""


def _launch_in_a_throwaway_process(pid_file: str, *, confirm: bool) -> tuple[int, int]:
    src_root = os.path.join(os.path.dirname(__file__), "..", "src")
    args = [sys.executable, "-c", _GRANDCHILD_LAUNCH_SCRIPT.format(src=src_root), pid_file]
    if confirm:
        args.append("confirm")
    subprocess.run(args, check=True, timeout=10)
    pid_str, pgid_str = open(pid_file).read().split()
    return int(pid_str), int(pgid_str)


@pytest.mark.unit
def test_a_confirmed_deferred_launch_survives_the_launching_process_exiting(tmp_path: Any) -> None:
    """F1: once the caller's durable record is down and confirms, the trampoline has
    disarmed itself — the launching process then exiting outright (a crash or graceful exit
    look alike to the OS) must no longer kill the child (`docs/deployment/recovery.md`)."""
    pid, pgid = _launch_in_a_throwaway_process(str(tmp_path / "pid"), confirm=True)
    try:
        assert _is_alive(pid), "a confirmed launch died when its launching process exited"
    finally:
        os.killpg(pgid, signal.SIGKILL)
        _reap(pid)


@pytest.mark.unit
def test_an_unconfirmed_deferred_launch_is_killed_when_the_launching_process_exits(tmp_path: Any) -> None:
    """F1's inverse: the narrow pre-confirmation window still works — a launch nobody ever
    confirms is still armed to its launching process, so that process's own exit (crash or
    graceful alike) still kills it outright, exactly as an un-deferred launch always has."""
    pid, pgid = _launch_in_a_throwaway_process(str(tmp_path / "pid"), confirm=False)
    try:
        deadline = time.monotonic() + 2.0
        while _is_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _is_alive(pid), "an unconfirmed launch outlived its launching process's exit"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        _reap(pid)


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
