"""The findings area's registrations: its read and write ports over the adapter it is handed, and its services.

The ports alias the one adapter instance the root hands in, so a reader and a writer in one tenant share it; the
area names no adapter. The store-free graph declares each of them absent."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import (
    FindingExitService,
    IFindingExitResolver,
    IReadFindingRepository,
    IWriteFindingRepository,
)
from blizzard.hub.tenancy import STORE_UNAVAILABLE

#: The finding reads a consumer depends on.
READ_FINDINGS = port(IReadFindingRepository)
#: The finding writes a consumer depends on.
WRITE_FINDINGS = port(IWriteFindingRepository)


def register(registry: Registry, findings: Ref[IWriteFindingRepository]) -> None:
    """Declare the finding ports over ``findings``, the exit verbs and the bucket reader, each once per tenant."""
    registry.alias(READ_FINDINGS, findings)
    registry.alias(WRITE_FINDINGS, findings)
    # What closes a delivered proposal's findings, by the port the proposal resolution takes.
    registry.alias(port(IFindingExitResolver), registry.service(FindingExitService, lifetime=Lifetime.TENANT))
    registry.service(FindingBucketReader, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports and services absent, for the store-free graph."""
    for missing in (
        IReadFindingRepository,
        IWriteFindingRepository,
        FindingExitService,
        IFindingExitResolver,
        FindingBucketReader,
    ):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
