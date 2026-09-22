"""Subprocess adapter for the one-shot process seam (package-private) — the reference
:class:`~blizzard.runner.subscriptions.one_shot_process.IOneShotProcess` binding."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Mapping, Sequence

from blizzard.foundation.logging import get_logger
from blizzard.runner.subscriptions.one_shot_process import IOneShotProcess, OneShotResult

_log = get_logger("blizzard.runner.subscriptions")


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
            process.kill()
            stdout, stderr = process.communicate()
            _log.warning("one-shot subprocess timed out", argv=list(argv), timeout=timeout)
            return OneShotResult(exit_code=None, stdout=stdout, stderr=stderr, timed_out=True)
        return OneShotResult(exit_code=process.returncode, stdout=stdout, stderr=stderr, timed_out=False)


def _conforms_one_shot_process(x: SubprocessOneShotProcess) -> IOneShotProcess:
    return x
