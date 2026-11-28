"""The hub client area's registrations: what a client verb's ``CliContext`` is built from, and that context."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.cli.collaborators import ClientFactory
from blizzard.cli.operator_trace import ITraceHeaders
from blizzard.foundation.operator_sessions import IReadSessionStore
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.foundation.roles import dto
from blizzard.hub.cli.context import CliContext
from blizzard.hub.cli.session_service import SessionService


@dto
@dataclass(frozen=True)
class ConnectionOptions:
    """The connection options a client verb was invoked with, as typed; ``None`` leaves each to its default."""

    hub_url: str | None
    as_json: bool
    tenant: str | None


#: The carrier's session service, which ``login`` and ``logout`` write through.
SESSION_SERVICE = port(SessionService)
#: The read half of that service.
SESSIONS = port(IReadSessionStore)
#: This invocation's connection options.
CONNECTION_OPTIONS = port(ConnectionOptions)


def _cli_context(
    options: ConnectionOptions, session_reader: IReadSessionStore, trace: ITraceHeaders, client_factory: ClientFactory
) -> CliContext:
    return CliContext.of(options.hub_url, session_reader, trace, client_factory, options.as_json, tenant=options.tenant)


def register(registry: Registry) -> None:
    """Declare the borrowed session service and options, and the command's ``CliContext`` over them."""
    service = registry.context(SESSION_SERVICE, lifetime=Lifetime.COMMAND)
    registry.alias(SESSIONS, service)
    registry.context(CONNECTION_OPTIONS, lifetime=Lifetime.COMMAND)
    registry.factory(_cli_context, lifetime=Lifetime.COMMAND)
