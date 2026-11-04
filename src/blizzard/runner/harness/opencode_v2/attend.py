"""Handing a session to the operator: its tree is stopped, best effort, and its rules replaced with the attended set
before the operator's own OpenCode opens it. A session whose rules cannot be replaced is never opened."""

from __future__ import annotations

import time
from collections.abc import Callable

from blizzard.runner.harness.opencode_v2.client import ServiceRequestError, ServiceShapeError
from blizzard.runner.harness.opencode_v2.driver import DriverCore
from blizzard.runner.harness.opencode_v2.request import ATTENDED_RULES
from blizzard.runner.harness.opencode_v2.transport import ServiceUnreachableError

DEFAULT_STOP_SECONDS = 10.0

_SERVICE_FAULTS = (ServiceRequestError, ServiceShapeError, ServiceUnreachableError)


class AttendError(RuntimeError):
    """The session cannot be handed over: it does not exist, or its rules could not be replaced."""


def attended_argv(binary: str, session_id: str) -> list[str]:
    return [binary, "--session", session_id]


class SessionHandover:
    def __init__(
        self,
        core: DriverCore,
        *,
        warn: Callable[[str], None],
        stop_seconds: float = DEFAULT_STOP_SECONDS,
        poll_seconds: float = 0.2,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._core = core
        self._warn = warn
        self._stop_seconds = stop_seconds
        self._poll_seconds = poll_seconds
        self._monotonic = monotonic
        self._sleep = sleep

    def prepare(self, session_id: str) -> None:
        """Make ``session_id`` the operator's: stop its tree and replace its rules; raises :class:`AttendError`."""
        client = self._core.client
        try:
            client.session(session_id)
        except ServiceRequestError as exc:
            if exc.status == 404:
                raise AttendError(f"the OpenCode service has no session {session_id}") from exc
            raise AttendError(f"cannot read session {session_id}: {exc}") from exc
        except (ServiceShapeError, ServiceUnreachableError) as exc:
            raise AttendError(f"cannot read session {session_id}: {exc}") from exc
        try:
            self._stop(session_id)
        except _SERVICE_FAULTS as exc:
            self._warn(f"could not stop session {session_id}'s tree, opening it anyway: {exc}")
        try:
            client.update_session(session_id, rules=ATTENDED_RULES)
        except _SERVICE_FAULTS as exc:
            raise AttendError(f"cannot replace session {session_id}'s rules with the attended set: {exc}") from exc

    def _stop(self, session_id: str) -> None:
        self._core.interrupt_tree(session_id)
        deadline = self._monotonic() + self._stop_seconds
        while self._core.tree_state(session_id).running:
            if self._monotonic() >= deadline:
                self._warn(f"session {session_id}'s tree still runs {self._stop_seconds:.0f}s after the interrupt")
                return
            self._sleep(self._poll_seconds)


__all__ = ["DEFAULT_STOP_SECONDS", "AttendError", "SessionHandover", "attended_argv"]
