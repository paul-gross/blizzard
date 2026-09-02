"""The subprocess binding for the OpenCode compatibility proof.

Only this module owns ``subprocess``.  The probe itself depends on the narrow process seam so
fixture-backed tests can remain in-process and the child environment can be inspected without
ever copying the runner's environment wholesale.
"""

from __future__ import annotations

import contextlib
import errno
import os
import pty
import select
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Protocol

from blizzard.runner.harness.internal.opencode_landlock import (
    LandlockPolicy,
    LandlockUnavailable,
    require_landlock,
)

_CAPTURE_LIMIT_BYTES = 4 * 1024 * 1024
_LINE_QUEUE_LIMIT_BYTES = 256 * 1024
_DRAIN_AFTER_STOP_SECONDS = 0.5


@dataclass(frozen=True)
class OpenCodeProcessResult:
    """Captured output from one attempted OpenCode command."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    process_group_reaped: bool = True
    output_truncated: bool = False
    start_failed: bool = False


class OpenCodeStartedProcess(Protocol):
    """The small process control surface needed by the interruption probe."""

    def poll(self) -> int | None:
        """Return the exit code, or ``None`` while the child is running."""
        ...

    def wait(self, timeout: float | None = None) -> int:
        """Wait for the child and return its exit code."""
        ...

    def terminate(self) -> None:
        """Ask the child to stop."""
        ...

    def kill(self) -> None:
        """Force the child to stop."""
        ...

    def read_line(self, timeout: float) -> str | None:
        """Read one captured stdout line, or return ``None`` before one is ready."""
        ...

    def write_input(self, value: str) -> None:
        """Write attended input to the controlling terminal."""
        ...

    def result(self, timeout: float) -> OpenCodeProcessResult:
        """Collect the process and its captured output, applying the timeout boundary."""
        ...

    def group_alive(self) -> bool:
        """Return whether this process group still has a member."""
        ...

    def close_streams(self) -> None:
        """Release the captured stdout/terminal handles this process holds."""
        ...


class IOpenCodeProcess(Protocol):
    """The process seam used by the concrete OpenCode probe."""

    def preflight(self, *, cwd: Path, env: Mapping[str, str]) -> None:
        """Prove that the child filesystem boundary is available before provider access."""
        ...

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: float,
    ) -> OpenCodeProcessResult:
        """Run a bounded command and capture its output."""
        ...

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> OpenCodeStartedProcess:
        """Start a command whose lifetime the caller controls."""
        ...

    def start_capture(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> OpenCodeStartedProcess:
        """Start a command with line-readable stdout for an in-flight transcript export."""
        ...

    def start_interactive(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> OpenCodeStartedProcess:
        """Start a command with a controlling terminal for an interactive attach."""
        ...


class OpenCodeProcessError(RuntimeError):
    """The process boundary could not start or control a command."""


class _ProcessHandle(Protocol):
    """The Popen-shaped portion shared by pipe and pseudo-terminal processes."""

    pid: int
    returncode: int | None

    @property
    def stdout(self) -> IO[bytes] | None: ...

    @property
    def stderr(self) -> IO[bytes] | None: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def communicate(self, input: bytes | None = None, timeout: float | None = None) -> tuple[bytes, bytes]: ...


class _PtyProcess:
    """A small Popen-shaped handle around the child returned by ``forkpty``."""

    @property
    def stdout(self) -> None:
        return None

    @property
    def stderr(self) -> None:
        return None

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        try:
            waited_pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return self.returncode
        if waited_pid == 0:
            return None
        self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        if timeout is None:
            _, status = os.waitpid(self.pid, 0)
            self.returncode = os.waitstatus_to_exitcode(status)
            return self.returncode
        deadline = time.monotonic() + timeout
        while True:
            result = self.poll()
            if result is not None:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(str(self.pid), timeout)
            time.sleep(min(0.01, remaining))

    def communicate(self, input: bytes | None = None, timeout: float | None = None) -> tuple[bytes, bytes]:
        """Satisfy the process handle seam; interactive callers read the master directly."""

        del input
        self.wait(timeout=timeout)
        return b"", b""


@dataclass
class _SubprocessStartedProcess:
    """A process-group handle so interruption does not leave OpenCode descendants behind."""

    process: _ProcessHandle
    capture_output: bool = False
    interactive_fd: int | None = None
    _stdout_capture: bytearray = field(default_factory=bytearray)
    _stderr_capture: bytearray = field(default_factory=bytearray)
    _stdout_pending: bytearray = field(default_factory=bytearray)
    _stdout_lines: list[str] = field(default_factory=list)
    _stdout_line_bytes: int = 0
    _stdout_eof: bool = False
    _stderr_eof: bool = False
    _output_truncated: bool = False

    def __post_init__(self) -> None:
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    os.set_blocking(stream.fileno(), False)
                except OSError as exc:
                    raise OpenCodeProcessError("OpenCode output pipes could not be made nonblocking") from exc

    def poll(self) -> int | None:
        return self.process.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self.process.wait(timeout=timeout)

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)

    def kill(self) -> None:
        self._signal(signal.SIGKILL)

    def read_line(self, timeout: float) -> str | None:
        """Read without blocking the probe past its polling interval."""

        if self.interactive_fd is not None:
            ready, _, _ = select.select([self.interactive_fd], [], [], max(0.0, timeout))
            if not ready:
                return None
            try:
                value = os.read(self.interactive_fd, 4096)
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF}:  # EIO is the normal pty EOF on POSIX.
                    return None
                if exc.errno == errno.EAGAIN:
                    return None
                raise
            if not value:
                return None
            self._append_interactive(value)
            return value.decode(errors="replace")

        if not self.capture_output or self.process.stdout is None:
            return None
        if self._stdout_lines:
            return self._pop_stdout_line()
        self._pump(timeout)
        return self._pop_stdout_line()

    def write_input(self, value: str) -> None:
        """Write one attended input value to the child's controlling terminal."""

        if self.interactive_fd is None:
            raise OpenCodeProcessError("the OpenCode process has no controlling terminal")
        if not isinstance(value, str) or not value:
            raise OpenCodeProcessError("interactive input must be a non-empty string")

        pending = value.encode()
        while pending:
            try:
                written = os.write(self.interactive_fd, pending)
            except OSError as exc:
                if exc.errno == errno.EAGAIN:
                    _, writable, _ = select.select([], [self.interactive_fd], [], 1.0)
                    if writable:
                        continue
                raise OpenCodeProcessError("interactive input could not be written") from exc
            if written <= 0:
                raise OpenCodeProcessError("interactive input could not be written")
            pending = pending[written:]

    def result(self, timeout: float) -> OpenCodeProcessResult:
        """Collect and, on timeout, kill/reap the whole process group."""

        try:
            return self._result(timeout)
        except BaseException:
            # A cancellation or Ctrl-C can interrupt wait/communicate before it reaps the
            # child.  The caller still receives the original interruption after this cleanup.
            _stop_process_group(self.process)
            self.close_streams()
            raise

    def _result(self, timeout: float) -> OpenCodeProcessResult:
        if self.interactive_fd is not None:
            deadline = time.monotonic() + timeout
            while self.process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    group_reaped = _stop_process_group(self.process)
                    self.close_streams()
                    return OpenCodeProcessResult(
                        -1,
                        self._stdout_text(),
                        "",
                        timed_out=True,
                        process_group_reaped=group_reaped,
                        output_truncated=self._output_truncated,
                    )
                time.sleep(min(0.01, remaining))
            self._drain_interactive()
            result = OpenCodeProcessResult(
                self.process.returncode if self.process.returncode is not None else -1,
                self._stdout_text(),
                "",
                output_truncated=self._output_truncated,
            )
            return self._finalize_group(result)

        if not self.capture_output:
            try:
                result = OpenCodeProcessResult(self.process.wait(timeout=timeout), "", "")
            except subprocess.TimeoutExpired:
                group_reaped = _stop_process_group(self.process)
                return OpenCodeProcessResult(-1, "", "", timed_out=True, process_group_reaped=group_reaped)
            return self._finalize_group(result)

        deadline = time.monotonic() + timeout
        timed_out = False
        group_reaped = True
        while True:
            self._pump(0.05)
            returncode = self.process.poll()
            if returncode is not None:
                if _process_group_exists(self.process.pid):
                    group_reaped = _stop_process_group(self.process)
                    if returncode == 0:
                        returncode = -1
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                group_reaped = _stop_process_group(self.process)
                returncode = -1
                break
            self._pump(min(0.05, remaining))

        self._drain_pipes(_DRAIN_AFTER_STOP_SECONDS)
        self._close_pipe_streams()
        if returncode is None:
            returncode = -1
        if self._output_truncated and returncode == 0:
            returncode = -1
        result = OpenCodeProcessResult(
            returncode,
            self._stdout_text(),
            self._stderr_text(),
            timed_out=timed_out,
            process_group_reaped=group_reaped,
            output_truncated=self._output_truncated,
        )
        return self._finalize_group(result)

    def _finalize_group(self, result: OpenCodeProcessResult) -> OpenCodeProcessResult:
        """Reject a direct-child success until every member of its process group is gone."""

        if not _process_group_exists(self.process.pid):
            return result
        group_reaped = _stop_process_group(self.process)
        return OpenCodeProcessResult(
            -1 if result.returncode == 0 else result.returncode,
            result.stdout,
            result.stderr,
            timed_out=result.timed_out,
            process_group_reaped=group_reaped,
            output_truncated=result.output_truncated,
        )

    def group_alive(self) -> bool:
        """Expose the process-group boundary to interruption probes."""

        return _process_group_exists(self.process.pid)

    def _signal(self, signum: signal.Signals) -> None:
        _signal_process_group(self.process.pid, signum)

    def close_streams(self) -> None:
        self._close_pipe_streams()
        if self.interactive_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self.interactive_fd)
            self.interactive_fd = None

    def _drain_interactive(self) -> None:
        if self.interactive_fd is None:
            return
        while True:
            ready, _, _ = select.select([self.interactive_fd], [], [], 0)
            if not ready:
                return
            try:
                value = os.read(self.interactive_fd, 4096)
            except OSError:
                return
            if not value:
                return
            self._append_interactive(value)

    def _pump(self, timeout: float) -> None:
        """Drain both pipes without ever using a buffered blocking file wrapper."""

        fds = self._open_pipe_fds()
        if not fds:
            return
        ready, _, _ = select.select(fds, [], [], max(0.0, timeout))
        for fd in ready:
            self._drain_fd(fd)

    def _drain_pipes(self, timeout: float) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        while self._open_pipe_fds() and time.monotonic() < deadline:
            self._pump(min(0.02, max(0.0, deadline - time.monotonic())))

    def _drain_fd(self, fd: int) -> None:
        for _ in range(16):
            try:
                value = os.read(fd, 64 * 1024)
            except OSError as exc:
                if exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    return
                self._mark_pipe_eof(fd)
                return
            if not value:
                self._mark_pipe_eof(fd)
                return
            if self.process.stdout is not None and fd == self.process.stdout.fileno():
                self._append_stdout(value)
            else:
                self._append_stderr(value)

    def _append_stdout(self, value: bytes) -> None:
        accepted = self._append_capture(self._stdout_capture, value)
        if accepted:
            self._stdout_pending.extend(accepted)
            if len(self._stdout_pending) > _CAPTURE_LIMIT_BYTES:
                del self._stdout_pending[_CAPTURE_LIMIT_BYTES:]
                self._output_truncated = True
            while True:
                try:
                    newline = self._stdout_pending.index(10)
                except ValueError:
                    return
                line = bytes(self._stdout_pending[: newline + 1])
                del self._stdout_pending[: newline + 1]
                if self._stdout_line_bytes + len(line) > _LINE_QUEUE_LIMIT_BYTES:
                    self._output_truncated = True
                    continue
                self._stdout_lines.append(line.decode(errors="replace"))
                self._stdout_line_bytes += len(line)

    def _append_stderr(self, value: bytes) -> None:
        self._append_capture(self._stderr_capture, value)

    def _append_interactive(self, value: bytes) -> None:
        self._append_capture(self._stdout_capture, value)

    def _append_capture(self, target: bytearray, value: bytes) -> bytes:
        remaining = _CAPTURE_LIMIT_BYTES - len(target)
        if remaining <= 0:
            self._output_truncated = True
            return b""
        accepted = value[:remaining]
        target.extend(accepted)
        if len(accepted) != len(value):
            self._output_truncated = True
        return accepted

    def _mark_pipe_eof(self, fd: int) -> None:
        if self.process.stdout is not None and fd == self.process.stdout.fileno():
            self._stdout_eof = True
            if self._stdout_pending:
                pending = bytes(self._stdout_pending)
                self._stdout_pending.clear()
                if self._stdout_line_bytes + len(pending) <= _LINE_QUEUE_LIMIT_BYTES:
                    self._stdout_lines.append(pending.decode(errors="replace"))
                    self._stdout_line_bytes += len(pending)
                else:
                    self._output_truncated = True
        elif self.process.stderr is not None and fd == self.process.stderr.fileno():
            self._stderr_eof = True

    def _open_pipe_fds(self) -> list[int]:
        fds: list[int] = []
        if self.process.stdout is not None and not self._stdout_eof:
            fds.append(self.process.stdout.fileno())
        if self.process.stderr is not None and not self._stderr_eof:
            fds.append(self.process.stderr.fileno())
        return fds

    def _pop_stdout_line(self) -> str | None:
        if not self._stdout_lines:
            return None
        line = self._stdout_lines.pop(0)
        self._stdout_line_bytes -= len(line.encode())
        return line

    def _stdout_text(self) -> str:
        return bytes(self._stdout_capture).decode(errors="replace")

    def _stderr_text(self) -> str:
        return bytes(self._stderr_capture).decode(errors="replace")

    def _close_pipe_streams(self) -> None:
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()


class SubprocessOpenCodeProcess:
    """Reference process binding: Landlock confinement, no shell, and bounded output."""

    def preflight(self, *, cwd: Path, env: Mapping[str, str]) -> None:
        """Fail before provider access when the inherited filesystem boundary is unavailable."""

        _require_landlock()
        try:
            process = self._popen(
                ["/bin/true"],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OpenCodeProcessError("OpenCode filesystem confinement preflight failed") from exc
        try:
            if process.wait(timeout=5.0) != 0:
                raise OpenCodeProcessError("OpenCode filesystem confinement preflight failed")
        except subprocess.TimeoutExpired as exc:
            _stop_process_group(process)
            raise OpenCodeProcessError("OpenCode filesystem confinement preflight timed out") from exc

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: float,
    ) -> OpenCodeProcessResult:
        try:
            process = self._popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OpenCodeProcessError("OpenCode command could not be started") from exc

        started = _SubprocessStartedProcess(process, capture_output=True)
        try:
            return started.result(timeout)
        except BaseException:
            _stop_process_group(process)
            started.close_streams()
            raise

    def start(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> OpenCodeStartedProcess:
        try:
            process = self._popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OpenCodeProcessError("OpenCode process could not be started") from exc
        try:
            return _SubprocessStartedProcess(process)
        except BaseException:
            _stop_process_group(process)
            raise

    def start_capture(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> OpenCodeStartedProcess:
        try:
            process = self._popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OpenCodeProcessError("OpenCode capture process could not be started") from exc
        try:
            return _SubprocessStartedProcess(process, capture_output=True)
        except BaseException:
            _stop_process_group(process)
            with contextlib.suppress(OSError):
                if process.stdout is not None:
                    process.stdout.close()
            with contextlib.suppress(OSError):
                if process.stderr is not None:
                    process.stderr.close()
            raise

    def start_interactive(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> OpenCodeStartedProcess:
        """Start an OpenCode TUI with a controlling terminal established by ``forkpty``."""

        _require_landlock()
        policy = LandlockPolicy(argv, cwd, env)
        try:
            child_pid, master_fd = pty.fork()
        except (AttributeError, OSError) as exc:
            raise OpenCodeProcessError("OpenCode interactive attach needs a POSIX pseudo-terminal") from exc
        if child_pid == 0:
            try:
                os.chdir(cwd)
                policy.apply()
                os.execvpe(str(argv[0]), list(argv), dict(env))
            except BaseException:
                os._exit(127)

        process = _PtyProcess(child_pid)
        started = _SubprocessStartedProcess(process, interactive_fd=master_fd)
        try:
            os.set_blocking(master_fd, False)
            _set_interactive_terminal_size(master_fd)
            return started
        except BaseException:
            _stop_process_group(process)
            started.close_streams()
            raise

    @staticmethod
    def _popen(
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        stdin: int,
        stdout: int,
        stderr: int,
    ) -> subprocess.Popen[bytes]:
        _require_landlock()
        policy = LandlockPolicy(argv, cwd, env)
        return subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=False,
            close_fds=True,
            start_new_session=True,
            preexec_fn=policy.apply,
        )


def _signal_process_group(pid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        return


def _stop_process_group(process: _ProcessHandle, grace_seconds: float = 0.5) -> bool:
    """Terminate, force, and reap a bounded command's complete process group."""

    pid = process.pid
    _signal_process_group(pid, signal.SIGTERM)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        _signal_process_group(pid, signal.SIGKILL)
        with contextlib.suppress(BaseException):
            process.wait()
    except BaseException:
        _signal_process_group(pid, signal.SIGKILL)
        with contextlib.suppress(BaseException):
            process.wait()
    if _process_group_exists(pid):
        _signal_process_group(pid, signal.SIGKILL)
        _wait_for_group_exit(pid, grace_seconds)
    return not _process_group_exists(pid)


def _process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_group_exit(pgid: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    try:
        while _process_group_exists(pgid) and time.monotonic() < deadline:
            time.sleep(0.01)
    except BaseException:
        return


def stop_started_process(child: OpenCodeStartedProcess) -> bool:
    """Terminate and reap a started process group, even when the caller was interrupted."""

    with contextlib.suppress(BaseException):
        # Signalling a reaped child reaches whatever recycled its pid, so never signal blind.
        if child.poll() is None:
            child.terminate()
    with contextlib.suppress(BaseException):
        child.wait(0.25)
    try:
        if child.poll() is None:
            with contextlib.suppress(BaseException):
                child.kill()
            with contextlib.suppress(BaseException):
                child.wait(0.25)
    except BaseException:
        pass
    try:
        group_alive = child.group_alive()
    except AttributeError:
        group_alive = False
    except BaseException:
        return False
    if group_alive:
        with contextlib.suppress(BaseException):
            child.kill()
        with contextlib.suppress(BaseException):
            child.wait(0.25)
        try:
            group_alive = child.group_alive()
        except BaseException:
            return False
    try:
        child.close_streams()
    except BaseException:
        return False
    try:
        return child.poll() is not None and not group_alive
    except BaseException:
        return False


def _set_interactive_terminal_size(fd: int) -> None:
    try:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    except (ImportError, OSError):
        pass


def _conforms_process(x: SubprocessOpenCodeProcess) -> IOpenCodeProcess:
    return x


__all__ = [
    "IOpenCodeProcess",
    "OpenCodeProcessError",
    "OpenCodeProcessResult",
    "OpenCodeStartedProcess",
    "SubprocessOpenCodeProcess",
    "stop_started_process",
]


def _require_landlock() -> None:
    """Report a missing boundary in the vocabulary this module's callers already handle."""

    try:
        require_landlock()
    except LandlockUnavailable as exc:
        raise OpenCodeProcessError(str(exc)) from exc
