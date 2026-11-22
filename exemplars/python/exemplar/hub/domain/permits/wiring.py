"""The permits area's registrations: its read and write ports over the adapter it is handed, and its services.

Both ports alias the one adapter instance the root hands in, so a reader and a writer in one tenant share it; the area
names no adapter."""

# exemplar: bzh:dependency-injection — a new service is one line here and one parameter at its consumer.

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from exemplar.hub.domain.permits.holders import PermitHolderReader
from exemplar.hub.domain.permits.model import IReadPermitRepository, IWritePermitRepository
from exemplar.hub.domain.permits.service import PermitService

#: The permit reads a consumer depends on.
READ_PERMITS = port(IReadPermitRepository)
#: The permit writes a consumer depends on.
WRITE_PERMITS = port(IWritePermitRepository)


def register(registry: Registry, permits: Ref[IWritePermitRepository]) -> None:
    """Declare the permit ports over ``permits``, the permit verbs and the holder reads, each once per tenant."""
    registry.alias(READ_PERMITS, permits)
    registry.alias(WRITE_PERMITS, permits)
    registry.service(PermitService, lifetime=Lifetime.TENANT)
    registry.service(PermitHolderReader, lifetime=Lifetime.TENANT)
