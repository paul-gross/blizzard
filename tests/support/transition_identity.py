"""The legacy bags' retained fields are the container's instances, and every port of an adapter is one object.

While an area migrates, a bag field the legacy builder still fills must be the very instance the tenant's container
scope holds, never a second copy; and each port an adapter is registered under resolves to that adapter's instance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from blizzard.hub.composition import TenantServices, TenantStores
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import IReadFindingRepository, IWriteFindingRepository
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import HubTransactions

#: Each adapter the tenant scope owns, and the ports it is registered under.
ADAPTER_PORTS: Mapping[type[Any], tuple[type[Any], ...]] = {
    ScopedConnections: (),
    HubTransactions: (IHubTransactions,),
    ProjectStore: (IReadProjectRepository,),
    FindingStore: (IReadFindingRepository, IWriteFindingRepository),
}


def transition_identity_violations(stores: TenantStores, services: TenantServices) -> list[str]:
    """Each retained bag field that is not its container instance, and each port that is not its adapter's."""
    tenant = stores.tenant
    retained: tuple[tuple[str, object, type[Any]], ...] = (
        ("TenantStores.connections", stores.connections, ScopedConnections),
        ("TenantStores.transactions", stores.transactions, HubTransactions),
        ("TenantStores.projects", stores.projects, ProjectStore),
        ("TenantStores.finding_store", stores.finding_store, FindingStore),
        ("TenantServices.projects", services.projects, IReadProjectRepository),
        ("TenantServices.findings", services.findings, IReadFindingRepository),
        ("TenantServices.finding_bucket", services.finding_bucket, FindingBucketReader),
    )
    problems = [
        f"{name} is not its container instance" for name, field, key in retained if field is not tenant.get(key)
    ]
    for adapter, ports in ADAPTER_PORTS.items():
        owned = tenant.get(adapter)
        problems.extend(
            f"{each.__qualname__} is not {adapter.__qualname__}'s instance"
            for each in ports
            if tenant.get(each) is not owned
        )
    return problems
