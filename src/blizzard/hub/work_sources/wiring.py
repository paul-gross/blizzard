"""The work-source area's registrations: each tenant's work-source registry over its configured records, and the work
ref label it renders, whose single owner this area is.

The registry builds each configured source's client through the tenant's built-object cache, gates its forge calls on
the tenant's forge budgets, and instruments each client through the process's tracing hook. Whether a close writes to
the forge is read from the live config source on each use. The store-free graph declares both registrations absent."""

from __future__ import annotations

from typing import Any, cast

from blizzard.foundation.platform_tracing.handle import IInstrumentClient
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.domain.chunk.model import WorkRefLabel
from blizzard.hub.domain.config.credentials import IReadCredentialRepository
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.work_sources import IReadWorkSourceRepository
from blizzard.hub.domain.garden.proposals.resolution import GardenProposalDeliveryResolution
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.domain.work_items.ports import IReadWorkItemRepository
from blizzard.hub.forge.auth import ForgeScope, IForgeAuthorizations, ScopedAuthorizations
from blizzard.hub.forge.budget import ForgeBudgets
from blizzard.hub.forge.refusals import refusal_reader
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.settings.source import HubConfigSource
from blizzard.hub.tenancy import STORE_UNAVAILABLE
from blizzard.hub.work_sources.internal.factory import WorkSourceEntry
from blizzard.hub.work_sources.source import IWorkSourceRegistry


def work_source_registry(
    records: IReadWorkSourceRepository,
    forges: IReadForgeRepository,
    credentials: IReadCredentialRepository,
    objects: ConfigObjectCache,
    authorizations: IForgeAuthorizations,
    users: IReadUserRepository,
    work_items: IReadWorkItemRepository,
    edits: WorkItemEditService,
    resolution: GardenProposalDeliveryResolution,
    budgets: ForgeBudgets,
    config: HubConfigSource,
    instrument: IInstrumentClient,
) -> IWorkSourceRegistry:
    """The tenant's work-source registry: its built-in hub source and each configured source it stores."""
    return WorkSourceEntry.registry(
        records=records,
        forges=forges,
        credentials=credentials,
        objects=objects,
        authorizations=ScopedAuthorizations(authorizations, ForgeScope.WORK_SOURCE),
        users=users,
        work_item_store=work_items,
        edits=edits,
        resolution=resolution,
        budgets=budgets,
        refusals=refusal_reader,
        close_forge_writes_enabled=lambda: config.current().close_forge_writes_enabled,
        instrument_client=instrument,
    )


def work_ref_label(registry: IWorkSourceRegistry) -> WorkRefLabel:
    """How the tenant renders a work ref's source-native token."""
    return registry.label


def register(registry: Registry) -> None:
    """Declare the tenant's work-source registry and its work ref label."""
    registry.factory(work_source_registry, lifetime=Lifetime.TENANT)
    registry.factory(work_ref_label, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the registry and the label absent, for the store-free graph."""
    registry.absent(port(IWorkSourceRegistry), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    # A callable alias keys the label, so it is named as its key rather than as a class.
    registry.absent(port(cast("type[Any]", WorkRefLabel)), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
