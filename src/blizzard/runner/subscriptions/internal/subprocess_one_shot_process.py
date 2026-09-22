"""Subprocess adapter for the one-shot process seam (package-private) — the reference
:class:`~blizzard.runner.subscriptions.one_shot_process.IOneShotProcess` binding (blizzard#504)."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence

from blizzard.foundation.logging import get_logger
from blizzard.runner.subscriptions.one_shot_process import IOneShotProcess, OneShotResult

_log = get_logger("blizzard.runner.subscriptions")


class SubprocessOneShotProcess:
    """Runs ``argv`` via ``subprocess.run``, feeding it ``stdin`` and closing it — the
    same one-request-then-EOF shape a JSON-RPC-over-stdio vendor CLI expects when it is
    given no ``initialize``d session to keep open past its last reply."""

    def run(
        self, argv: Sequence[str], *, stdin: str, timeout: float, env: Mapping[str, str] | None = None
    ) -> OneShotResult:
        try:
            result = subprocess.run(
                list(argv),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=dict(env) if env is not None else None,
            )
        except subprocess.TimeoutExpired as exc:
            _log.warning("one-shot subprocess timed out", argv=list(argv), timeout=timeout)
            return OneShotResult(
                exit_code=None,
                stdout=_decoded(exc.stdout),
                stderr=_decoded(exc.stderr),
                timed_out=True,
            )
        except OSError as exc:
            _log.warning("one-shot subprocess failed to launch", argv=list(argv), detail=str(exc))
            return OneShotResult(exit_code=None, stdout="", stderr=str(exc), timed_out=False)
        return OneShotResult(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr, timed_out=False)


def _decoded(stream: bytes | str | None) -> str:
    """``subprocess.TimeoutExpired`` carries partial output as ``bytes`` (or ``None``)
    even under ``text=True``; normalize either to ``str``."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def _conforms_one_shot_process(x: SubprocessOneShotProcess) -> IOneShotProcess:
    return x
