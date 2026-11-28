"""The shared CLI area's registrations: the borrowed values a migrated command's requests are made with."""

from __future__ import annotations

from blizzard.cli.collaborators import ClientFactory
from blizzard.cli.operator_trace import ITraceHeaders
from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Registry, port

#: The carrier's HTTP client factory.
CLIENT_FACTORY = port(ClientFactory)
#: The trace headers of the operator span the command's group opened.
TRACE_HEADERS = port(ITraceHeaders)
#: The clock a verb judges a time the operator typed against, distinct from the one that stamps spans.
WALL_CLOCK = port(IClock)


def register(registry: Registry) -> None:
    """Declare the borrowed client factory, trace headers and wall clock, none closed by the command."""
    registry.context(CLIENT_FACTORY, lifetime=Lifetime.COMMAND)
    registry.context(TRACE_HEADERS, lifetime=Lifetime.COMMAND)
    registry.context(WALL_CLOCK, lifetime=Lifetime.COMMAND)
