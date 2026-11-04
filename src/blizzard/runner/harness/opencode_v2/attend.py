"""Handing a fleet session to the operator: its tree is stopped, best effort, its rules replaced with the attended set,
and its model reasserted before the operator's own OpenCode opens it. A session whose rules cannot be replaced, or one
no blizzard runner minted, is never opened."""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from blizzard.runner.harness.opencode_v2.client import ModelReference, ServiceRequestError, ServiceShapeError
from blizzard.runner.harness.opencode_v2.driver import DriverCore
from blizzard.runner.harness.opencode_v2.request import ATTENDED_RULES, METADATA_KEY
from blizzard.runner.harness.opencode_v2.shapes import OpenCodeTwoShapeError, SessionRecord, parse_session
from blizzard.runner.harness.opencode_v2.transport import ServiceUnreachableError

DEFAULT_STOP_SECONDS = 10.0
DEFAULT_OPENCODE_BINARY = "opencode"
_MODEL_OPTION = re.compile(r"(?P<provider>[^/\s#]+)/(?P<model>[^\s#]+)(?:#(?P<variant>\S+))?")

_SERVICE_FAULTS = (ServiceRequestError, ServiceShapeError, ServiceUnreachableError)


class AttendError(RuntimeError):
    """The session cannot be handed over: it does not exist, is not a fleet session, or its rules could not be
    replaced."""


def attended_argv(binary: str, session_id: str) -> list[str]:
    return [binary, "--session", session_id]


def model_option(reference: ModelReference) -> str:
    """``provider/model[#variant]``, the form :func:`parse_model_option` reads back."""
    suffix = f"#{reference.variant}" if reference.variant else ""
    return f"{reference.provider_id}/{reference.id}{suffix}"


def parse_model_option(text: str) -> ModelReference:
    match = _MODEL_OPTION.fullmatch(text)
    if match is None:
        raise ValueError(f"{text!r} is not provider/model[#variant]")
    return ModelReference(match["provider"], match["model"], match["variant"])


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

    def prepare(self, session_id: str, model: ModelReference | None = None) -> None:
        """Make ``session_id`` the operator's: stop its tree, replace its rules, and switch it to ``model`` when it
        runs another; raises :class:`AttendError`."""
        client = self._core.client
        session = self._fleet_session(session_id)
        try:
            self._stop(session_id)
        except _SERVICE_FAULTS as exc:
            self._warn(f"could not stop session {session_id}'s tree, opening it anyway: {exc}")
        try:
            client.update_session(session_id, rules=ATTENDED_RULES)
        except _SERVICE_FAULTS as exc:
            raise AttendError(f"cannot replace session {session_id}'s rules with the attended set: {exc}") from exc
        if model is None or session.model == model:
            return
        try:
            client.switch_model(session_id, model)
        except _SERVICE_FAULTS as exc:
            self._warn(f"could not switch session {session_id} to {model_option(model)}, opening it anyway: {exc}")

    def _fleet_session(self, session_id: str) -> SessionRecord:
        try:
            session = parse_session(self._core.client.session(session_id))
        except ServiceRequestError as exc:
            if exc.status == 404:
                raise AttendError(f"the OpenCode service has no session {session_id}") from exc
            raise AttendError(f"cannot read session {session_id}: {exc}") from exc
        except (ServiceShapeError, ServiceUnreachableError, OpenCodeTwoShapeError) as exc:
            raise AttendError(f"cannot read session {session_id}: {exc}") from exc
        if METADATA_KEY not in session.metadata:
            raise AttendError(f"session {session_id} was not minted by a blizzard runner; it is not one to take over")
        return session

    def _stop(self, session_id: str) -> None:
        self._core.interrupt_tree(session_id)
        deadline = self._monotonic() + self._stop_seconds
        while self._core.tree_state(session_id).running:
            if self._monotonic() >= deadline:
                self._warn(f"session {session_id}'s tree still runs {self._stop_seconds:.0f}s after the interrupt")
                return
            self._sleep(self._poll_seconds)


__all__ = [
    "DEFAULT_OPENCODE_BINARY",
    "DEFAULT_STOP_SECONDS",
    "AttendError",
    "SessionHandover",
    "attended_argv",
    "model_option",
    "parse_model_option",
]
