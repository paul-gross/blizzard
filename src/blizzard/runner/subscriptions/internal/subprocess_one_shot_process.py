"""Subprocess adapter for the one-shot process seam (package-private) — the reference
:class:`~blizzard.runner.subscriptions.one_shot_process.IOneShotProcess` binding."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence

from blizzard.foundation.logging import get_logger
from blizzard.runner.subscriptions.one_shot_process import IOneShotProcess, OneShotResult

_log = get_logger("blizzard.runner.subscriptions")

# SIGTERM lets the vendor CLI finish an in-flight credential write; SIGKILL follows only if it lingers.
_TERMINATE_GRACE_SECONDS = 2.0


class SubprocessOneShotProcess:
    """Runs ``argv`` via ``subprocess.Popen``: writes ``stdin``, waits ``settle_seconds``, then
    closes it — EOF delayed so a reply to an in-flight request can land first. ``stdin`` is
    assumed small (a JSON-RPC handshake, not a data transfer), so the write fits the OS pipe
    buffer without a concurrent reader and never blocks on a full stdout pipe."""

    def run(
        self, argv: Sequence[str], *, stdin: str, timeout: float, env: Mapping[str, str], settle_seconds: float = 0.0
    ) -> OneShotResult:
        try:
            process = subprocess.Popen(
                list(argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=dict(env),
                start_new_session=True,
            )
        except OSError as exc:
            _log.warning("one-shot subprocess failed to launch", argv=list(argv), detail=str(exc))
            return OneShotResult(exit_code=None, stdout="", stderr=str(exc), timed_out=False)

        deadline = time.monotonic() + timeout
        assert process.stdin is not None
        try:
            process.stdin.write(stdin)
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # the child may already have exited or closed its own stdin

        if settle_seconds > 0:
            time.sleep(max(0.0, min(settle_seconds, deadline - time.monotonic())))

        try:
            stdout, stderr = process.communicate(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            stdout, stderr = self._terminate_group(process)
            _log.warning("one-shot subprocess timed out", argv=list(argv), timeout=timeout)
            return OneShotResult(exit_code=None, stdout=stdout, stderr=stderr, timed_out=True)
        return OneShotResult(exit_code=process.returncode, stdout=stdout, stderr=stderr, timed_out=False)

    @staticmethod
    def _terminate_group(process: subprocess.Popen[str]) -> tuple[str, str]:
        """SIGTERM the child's whole process group (it leads its own session), then SIGKILL the
        group after a grace period — reaching an npm-shim's real app-server, and sparing a
        credential file a hard kill mid-write."""
        _signal_group(process, signal.SIGTERM)
        try:
            return process.communicate(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            return process.communicate()


def _signal_group(process: subprocess.Popen[str], sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):  # the group already exited
        os.killpg(process.pid, sig)


def _conforms_one_shot_process(x: SubprocessOneShotProcess) -> IOneShotProcess:
    return x
