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

from blizzard.runner.harness.process_launch import LaunchedProcess, ProcessLauncher, _ensure_executable
from blizzard.runner.loop.process import LinuxProcessProbe


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
from blizzard.runner.harness.process_launch import ProcessLauncher
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
    with open(pid_file) as f:
        pid_str, pgid_str = f.read().split()
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


def _write_fd_of(launched: LaunchedProcess) -> int:
    """The private ``write_fd`` a launch's ``confirm_durable`` closure holds — reached by
    introspection so the EOF path below can be proven with nothing standing in for a real
    confirm byte, rather than adding a test-only accessor to production code."""
    closure = launched.confirm_durable.__closure__
    assert closure is not None  # a deferred launch's confirm_durable always closes over write_fd
    freevars = dict(zip(launched.confirm_durable.__code__.co_freevars, (c.cell_contents for c in closure), strict=True))
    return int(freevars["write_fd"])


@pytest.mark.unit
def test_control_pipe_eof_with_no_confirm_byte_kills_the_trampoline_instead_of_exec(tmp_path: Any) -> None:
    """F2: `os.read` returns `b""`, not an exception, on EOF — the trampoline must tell that
    apart from a real confirm byte. This test's own process never exits, so the only signal
    reaching the trampoline is the pipe closing in-process, isolated from the PDEATHSIG race."""
    sentinel = tmp_path / "ran"
    launcher = ProcessLauncher(LinuxProcessProbe())
    launched = launcher.launch(
        [sys.executable, "-c", f"import time; open({str(sentinel)!r}, 'w').close(); time.sleep(30)"],
        cwd=None,
        env=dict(os.environ),
        stdout=None,
        stderr=None,
        defer_disarm=True,
    )
    try:
        os.close(_write_fd_of(launched))
        # This test, not a throwaway grandchild, forked the trampoline directly — it must
        # reap it itself, or an un-reaped zombie still reads as "alive" to `os.kill(pid, 0)`.
        deadline = time.monotonic() + 2.0
        exited = False
        while time.monotonic() < deadline:
            reaped_pid, _status = os.waitpid(launched.pid, os.WNOHANG)
            if reaped_pid == launched.pid:
                exited = True
                break
            time.sleep(0.05)
        assert exited, "the trampoline outlived a control-pipe EOF with no confirm byte"
        assert not sentinel.exists(), "the real binary ran despite no confirm byte ever being sent"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(launched.pgid, signal.SIGKILL)


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


# --------------------------------------------------------------------------- #
# `_ensure_executable`: a missing path is `ENOENT`; existing-but-not-executable is `EACCES`.


@pytest.mark.unit
def test_ensure_executable_missing_path_raises_file_not_found(tmp_path: Any) -> None:
    missing = str(tmp_path / "does-not-exist")
    with pytest.raises(FileNotFoundError):
        _ensure_executable(missing, cwd=None, env={})


@pytest.mark.unit
def test_ensure_executable_existing_but_not_executable_raises_permission_error(tmp_path: Any) -> None:
    """Real ``execvp`` reports ``EACCES`` for an existing-but-not-executable file, not
    ``ENOENT`` — distinct from a genuinely missing path (above)."""
    not_executable = tmp_path / "not-executable"
    not_executable.write_text("#!/bin/sh\necho hi\n")
    not_executable.chmod(0o644)

    with pytest.raises(PermissionError):
        _ensure_executable(str(not_executable), cwd=None, env={})
