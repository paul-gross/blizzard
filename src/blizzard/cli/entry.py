"""The console scripts' process frame: orderly shutdown, then an exit that skips interpreter finalization.

``blizzard``, ``blizzard-hub`` and ``blizzard-runner`` end with ``os._exit`` once CPython's orderly
shutdown has done what matters: non-daemon threads joined, every other ``atexit`` handler run, and
``sys.stdout`` / ``sys.stderr`` flushed. Finalization's teardown of the loaded module graph is pure
latency for a process that is done. The exit code is the one click and CPython would have produced.

The fast exit is the first ``atexit`` registration, so it runs last, after every handler a later
import registers (``logging``'s included). That ordering is load-bearing: this module's top-level
imports are stdlib only, and everything else is imported inside the function, after arming."""

from __future__ import annotations

import atexit
import importlib
import os
import sys
from collections.abc import Callable
from typing import NoReturn

_FLUSH_FAILED = 120  # the status Py_FinalizeEx exits with when it cannot flush stdio


def _arm_fast_exit() -> Callable[[BaseException | None], None]:
    """Register the fast exit now; the returned callable records the code it will exit with."""
    code = [0]

    def record(exc: BaseException | None) -> None:
        if exc is not None:
            from blizzard.cli.operator_trace import exit_code_of

            code[0] = exit_code_of(exc)

    def fast_exit() -> NoReturn:
        try:
            for stream in (sys.stdout, sys.stderr):
                if stream is not None:  # detached stdio has nothing to flush
                    stream.flush()
        except (OSError, ValueError):
            os._exit(_FLUSH_FAILED)
        os._exit(code[0])

    atexit.register(fast_exit)
    return record


def _run(target: str) -> None:
    record = _arm_fast_exit()
    module_name, _, attr = target.partition(":")
    group = getattr(importlib.import_module(module_name), attr)
    try:
        group()
    except BaseException as exc:
        record(exc)
        raise


def blizzard() -> None:
    """The ``blizzard`` console script."""
    _run("blizzard.cli.main:blizzard")


def hub() -> None:
    """The ``blizzard-hub`` console script."""
    _run("blizzard.hub.cli:hub")


def runner() -> None:
    """The ``blizzard-runner`` console script."""
    _run("blizzard.runner.cli:runner")
