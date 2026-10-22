"""Launch-seam adapter for the one-shot process seam (package-private) — the reference
:class:`~blizzard.runner.subscriptions.one_shot_process.IOneShotProcess` binding."""

from __future__ import annotations

from collections.abc import Sequence

from blizzard.foundation.logging import get_logger
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.subscriptions.one_shot_process import IOneShotProcess, OneShotResult

_log = get_logger("blizzard.runner.subscriptions")


class SubprocessOneShotProcess:
    """Runs ``argv`` through the launch seam: writes ``stdin``, waits ``settle_seconds``, then
    closes it — EOF delayed so a reply to an in-flight request can land first. ``stdin`` is
    assumed small (a JSON-RPC handshake, not a data transfer), so the write fits the OS pipe
    buffer without a concurrent reader and never blocks on a full stdout pipe."""

    def __init__(self, children: IChildProcesses) -> None:
        self._children = children

    def run(
        self, argv: Sequence[str], *, stdin: str, timeout: float, env: ChildEnv, settle_seconds: float = 0.0
    ) -> OneShotResult:
        try:
            result = self._children.run(
                argv, cwd=None, env=env, timeout=timeout, input=stdin, input_settle=settle_seconds
            )
        except OSError as exc:
            _log.warning("one-shot subprocess failed to launch", argv=list(argv), detail=str(exc))
            return OneShotResult(exit_code=None, stdout="", stderr=str(exc), timed_out=False)
        if result.timed_out:
            _log.warning("one-shot subprocess timed out", argv=list(argv), timeout=timeout)
        return OneShotResult(
            exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr, timed_out=result.timed_out
        )


def _conforms_one_shot_process(x: SubprocessOneShotProcess) -> IOneShotProcess:
    return x
