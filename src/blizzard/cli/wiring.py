"""The shared CLI area's registrations: the borrowed values a migrated command's requests are made with."""

from __future__ import annotations

from blizzard.cli.collaborators import ClientFactory
from blizzard.cli.operator_trace import ITraceHeaders
from blizzard.foundation.registry.interface import Lifetime, Registry, port

#: The carrier's HTTP client factory.
CLIENT_FACTORY = port(ClientFactory)
#: The trace headers of the operator span the command's group opened.
TRACE_HEADERS = port(ITraceHeaders)


def register(registry: Registry) -> None:
    """Declare the borrowed client factory and trace headers, neither closed by the command."""
    registry.context(CLIENT_FACTORY, lifetime=Lifetime.COMMAND)
    registry.context(TRACE_HEADERS, lifetime=Lifetime.COMMAND)
