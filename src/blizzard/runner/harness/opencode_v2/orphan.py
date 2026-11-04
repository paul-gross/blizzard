"""Stopping the turn of a worker the runner killed: a killed driver leaves its turn running on the operator's service,
so its session tree is interrupted through the API — best effort, every request bounded, and never a service started
to do it. A failure is logged and left to reconciliation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from dataclasses import replace
from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.opencode_v2.client import ServiceRequestError, ServiceShapeError
from blizzard.runner.harness.opencode_v2.driver import (
    DriverCore,
    RegistrationSource,
    ServiceIdentityError,
    ServiceNotRegisteredError,
)
from blizzard.runner.harness.opencode_v2.registration import RegistrationError
from blizzard.runner.harness.opencode_v2.transport import (
    IServiceTransport,
    NonLoopbackServiceError,
    ServiceRequest,
    ServiceResponse,
    ServiceUnreachableError,
)

_log = get_logger("blizzard.runner.harness")

DEFAULT_ORPHAN_TIMEOUT_SECONDS = 5.0

_FAULTS = (
    ServiceNotRegisteredError,
    ServiceIdentityError,
    ServiceUnreachableError,
    ServiceRequestError,
    ServiceShapeError,
    RegistrationError,
    NonLoopbackServiceError,
)


class _BoundedTransport:
    """``inner``, with every request's timeout held to ``timeout_seconds``."""

    def __init__(self, inner: IServiceTransport, timeout_seconds: float) -> None:
        self._inner = inner
        self._timeout_seconds = timeout_seconds

    def _bounded(self, request: ServiceRequest) -> ServiceRequest:
        return replace(request, timeout_seconds=min(request.timeout_seconds, self._timeout_seconds))

    def send(self, request: ServiceRequest) -> ServiceResponse:
        return self._inner.send(self._bounded(request))

    def stream(self, request: ServiceRequest) -> AbstractContextManager[Iterator[bytes]]:
        return self._inner.stream(self._bounded(request))


class ISessionOrphaner(Protocol):
    def orphan(self, session_id: str) -> None:
        """Interrupt ``session_id``'s tree, best effort and bounded; never raises."""
        ...


class SessionOrphaner:
    def __init__(
        self,
        source: RegistrationSource,
        transport: IServiceTransport,
        *,
        timeout_seconds: float = DEFAULT_ORPHAN_TIMEOUT_SECONDS,
    ) -> None:
        self._source = source
        self._transport = _BoundedTransport(transport, timeout_seconds)

    def orphan(self, session_id: str) -> None:
        """Interrupt ``session_id``'s running descendants, deepest first, then the session; never raises."""
        try:
            stopped = DriverCore(self._source, self._transport, poll_seconds=0.0).interrupt_tree(session_id)
        except _FAULTS as exc:
            _log.warning("could not interrupt an orphaned OpenCode 2 session", session_id=session_id, detail=str(exc))
            return
        _log.info("interrupted an orphaned OpenCode 2 session", session_id=session_id, interrupted=len(stopped))


__all__ = ["DEFAULT_ORPHAN_TIMEOUT_SECONDS", "ISessionOrphaner", "SessionOrphaner"]
