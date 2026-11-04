"""The service-facing core of OpenCode 2 turns: binding to the operator's service by its identity, recognizing its
restart or loss, opening a caller-named session, walking and stopping its tree through the API, and the compatibility
proof's settle wait. The turn driver owns a driven turn's own completion rule."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.json_value import JsonObject
from blizzard.runner.harness.opencode_v2.client import (
    OpenCodeServiceClient,
    ServiceRequestError,
    ServiceShapeError,
    SessionCreate,
)
from blizzard.runner.harness.opencode_v2.registration import RegistrationError, ServiceRegistration
from blizzard.runner.harness.opencode_v2.transport import (
    IServiceTransport,
    NonLoopbackServiceError,
    ServiceUnreachableError,
)

DEFAULT_POLL_SECONDS = 1.0
DEFAULT_REACH_BOUND_SECONDS = 30.0
DEFAULT_RETURN_GRACE_SECONDS = 30.0


class ServiceNotRegisteredError(RuntimeError):
    """No managed service is registered for this user."""


class ServiceIdentityError(RuntimeError):
    """The service answering at the registered address is not the registered one."""


class ServiceRestartedError(RuntimeError):
    """The service the turn started on is gone: a different service lifetime now answers, or none does."""


class TurnTimeoutError(TimeoutError):
    """The session tree did not settle within the bound."""


@domain_model
@dataclass(frozen=True)
class ServiceIdentity:
    """One service lifetime: its registration ``id`` is new on every start and its ``pid`` changes with it."""

    registration_id: str
    pid: int
    version: str


@domain_model
@dataclass(frozen=True)
class TreeState:
    """What a poll saw of a root and its descendants."""

    session_ids: tuple[str, ...]
    running: tuple[str, ...]
    inbox: int

    def settled(self) -> bool:
        return not self.running and self.inbox == 0


type RegistrationSource = Callable[[], ServiceRegistration | None]


def bind_service(
    source: RegistrationSource, transport: IServiceTransport
) -> tuple[OpenCodeServiceClient, ServiceIdentity]:
    """A client of the registered service and that service's identity, refused unless ``GET /api/info`` reports the
    registered ``pid`` and ``version``."""
    registration = source()
    if registration is None:
        raise ServiceNotRegisteredError("no OpenCode service is registered")
    client = OpenCodeServiceClient(registration, transport)
    info = client.info()
    if info.pid != registration.pid or info.version != registration.version:
        raise ServiceIdentityError(
            f"the service at the registered address reports pid {info.pid} version {info.version}, "
            f"not the registered pid {registration.pid} version {registration.version}"
        )
    return client, ServiceIdentity(registration.id, registration.pid, registration.version)


class DriverCore:
    """Drives sessions on the service it was bound to, and recognizes that service's restart by its identity rather
    than by an outage, since a restart can finish between two polls."""

    def __init__(
        self,
        source: RegistrationSource,
        transport: IServiceTransport,
        *,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        reach_bound_seconds: float = DEFAULT_REACH_BOUND_SECONDS,
        return_grace_seconds: float = DEFAULT_RETURN_GRACE_SECONDS,
    ) -> None:
        self._source = source
        self._transport = transport
        self._poll_seconds = poll_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._reach_bound_seconds = reach_bound_seconds
        self._return_grace_seconds = return_grace_seconds
        self._unreachable_since: float | None = None
        self.client, self.identity = bind_service(source, transport)

    def current_identity(self) -> ServiceIdentity | None:
        """The identity of whatever service is registered and answering now, or ``None`` when none is — including a
        registration read mid-rewrite or an answer the core cannot read."""
        try:
            _, identity = bind_service(self._source, self._transport)
        except (
            ServiceNotRegisteredError,
            ServiceIdentityError,
            ServiceUnreachableError,
            ServiceRequestError,
            ServiceShapeError,
            RegistrationError,
            NonLoopbackServiceError,
        ):
            return None
        return identity

    def open_session(self, spec: SessionCreate, environment: Mapping[str, str]) -> JsonObject:
        """Create the caller-named root and replace its shell environment; a child never inherits it."""
        created = self.client.create_session(spec)
        self.client.set_environment(spec.id, environment)
        return created

    def tree(self, root_id: str) -> tuple[str, ...]:
        """The root and every descendant, parents before children."""
        ordered = [root_id]
        index = 0
        while index < len(ordered):
            ordered += [str(child["id"]) for child in self.client.children(ordered[index]) if "id" in child]
            index += 1
        return tuple(ordered)

    def tree_state(self, root_id: str) -> TreeState:
        ids = self.tree(root_id)
        running = self.client.running_sessions()
        return TreeState(ids, tuple(sid for sid in ids if sid in running), len(self.client.inbox(root_id)))

    def check_service(self, root_id: str) -> bool:
        """Whether the service this core was bound to answers now; raise :class:`ServiceRestartedError` once it is
        gone. A different service lifetime is gone at once; a service that stops answering is gone after the reach
        bound, and one that comes back as itself within it is not. A restarted service resumes the turn it
        interrupted by itself, without the session's environment, so the core interrupts the tree on the new service
        before raising — and past the bound it keeps looking for one through the return grace."""
        current = self.current_identity()
        if current == self.identity:
            self._unreachable_since = None
            return True
        if current is None:
            now = self._monotonic()
            if self._unreachable_since is None:
                self._unreachable_since = now
            if now - self._unreachable_since < self._reach_bound_seconds:
                return False
            self._interrupt_on_return(root_id)
            raise ServiceRestartedError("the OpenCode service stopped answering")
        self.rebind()
        with suppress(ServiceUnreachableError, ServiceRequestError):
            self.interrupt_tree(root_id)
        raise ServiceRestartedError("the OpenCode service restarted mid-turn")

    def _interrupt_on_return(self, root_id: str) -> None:
        deadline = self._monotonic() + self._return_grace_seconds
        while self._monotonic() < deadline:
            if self.current_identity() is not None:
                with suppress(ServiceUnreachableError, ServiceRequestError, ServiceShapeError, RegistrationError):
                    self.rebind()
                    self.interrupt_tree(root_id)
                return
            self._sleep(self._poll_seconds or 0.2)

    def await_settled(self, root_id: str, timeout_seconds: float) -> TreeState:
        """Poll until the root is idle, no descendant runs, and the root's inbox is empty, observed after the last
        descendant settled. Polling alone decides it; the event stream is never the record. A poll the service does
        not answer is retried until the reach bound."""
        deadline = self._monotonic() + timeout_seconds
        while True:
            if self.check_service(root_id):
                try:
                    state = self.tree_state(root_id)
                except ServiceUnreachableError:
                    state = None
                if state is not None and state.settled():
                    return state
            if self._monotonic() >= deadline:
                raise TurnTimeoutError(f"session {root_id} did not settle within {timeout_seconds:.0f}s")
            self._sleep(self._poll_seconds)

    def interrupt_tree(self, root_id: str) -> tuple[str, ...]:
        """Interrupt every running descendant, deepest first, then the root; returns what was asked to stop."""
        ids = self.tree(root_id)
        running = self.client.running_sessions()
        stopped = [sid for sid in reversed(ids[1:]) if sid in running]
        for sid in stopped:
            self.client.interrupt(sid)
        self.client.interrupt(root_id)
        return (*stopped, root_id)

    def rebind(self) -> None:
        """Bind to whichever service is registered now — after a restart, to stop what it resumed."""
        self.client, self.identity = bind_service(self._source, self._transport)


__all__ = [
    "DEFAULT_POLL_SECONDS",
    "DEFAULT_REACH_BOUND_SECONDS",
    "DEFAULT_RETURN_GRACE_SECONDS",
    "DriverCore",
    "RegistrationSource",
    "ServiceIdentity",
    "ServiceIdentityError",
    "ServiceNotRegisteredError",
    "ServiceRestartedError",
    "TreeState",
    "TurnTimeoutError",
    "bind_service",
]
