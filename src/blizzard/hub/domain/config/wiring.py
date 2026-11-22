"""The config area's registrations: its record, secret and reference ports over the adapters it is handed, and the one
configured-record writer.

Each port aliases the one adapter instance the root hands in, so a reader and a writer in one tenant share it; the area
names no adapter. The store-free graph declares each registration absent, from the same roster."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.foundation.secrets.sealing import ISealedSecretRepository
from blizzard.hub.domain.config.apply import IConfigApplyWriter
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.capabilities import IForgeOffers
from blizzard.hub.domain.config.carry_over import IConfigImportWriter
from blizzard.hub.domain.config.changes import IReadConfigChanges, ISecretReferences
from blizzard.hub.domain.config.credentials import (
    ICredentialReferences,
    IReadCredentialRepository,
    IWriteCredentialRepository,
)
from blizzard.hub.domain.config.forges import (
    ForgeKindOffers,
    IForgeReferences,
    IReadForgeRepository,
    IWriteForgeRepository,
)
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository, IWriteRepositoryRecordRepository
from blizzard.hub.domain.config.revisions import IReadConfigRevisions
from blizzard.hub.domain.config.secrets import ISecretCatalog, IWriteSecretRepository
from blizzard.hub.domain.config.work_sources import IReadWorkSourceRepository, IWriteWorkSourceRepository
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def forge_offers() -> IForgeOffers:
    """What each forge kind offers, from configuration — never the runtime offer, so a write reveals no secret."""
    return ForgeKindOffers()


def register(
    registry: Registry,
    *,
    work_sources: Ref[IWriteWorkSourceRepository],
    repositories: Ref[IWriteRepositoryRecordRepository],
    forges: Ref[IWriteForgeRepository],
    credentials: Ref[IWriteCredentialRepository],
    secrets: Ref[IWriteSecretRepository],
    sealed: Ref[ISealedSecretRepository],
    secret_references: Ref[ISecretReferences],
    forge_references: Ref[IForgeReferences],
    credential_references: Ref[ICredentialReferences],
    changes: Ref[IReadConfigChanges],
    apply_writer: Ref[IConfigApplyWriter],
    import_writer: Ref[IConfigImportWriter],
    revisions: Ref[IReadConfigRevisions],
) -> None:
    """Declare the config ports over the adapters handed in — each typed by the widest port it answers to, the secret
    store handed in as both ``secrets`` and ``sealed`` — and the writer, each once per tenant."""
    registry.alias(port(IReadWorkSourceRepository), work_sources)
    registry.alias(port(IWriteWorkSourceRepository), work_sources)
    registry.alias(port(IReadRepositoryRecordRepository), repositories)
    registry.alias(port(IWriteRepositoryRecordRepository), repositories)
    registry.alias(port(IReadForgeRepository), forges)
    registry.alias(port(IWriteForgeRepository), forges)
    registry.alias(port(IReadCredentialRepository), credentials)
    registry.alias(port(IWriteCredentialRepository), credentials)
    registry.alias(port(ISecretCatalog), secrets)
    registry.alias(port(IWriteSecretRepository), secrets)
    registry.alias(port(ISealedSecretRepository), sealed)
    registry.alias(port(ISecretReferences), secret_references)
    registry.alias(port(IForgeReferences), forge_references)
    registry.alias(port(ICredentialReferences), credential_references)
    registry.alias(port(IReadConfigChanges), changes)
    registry.alias(port(IConfigApplyWriter), apply_writer)
    registry.alias(port(IConfigImportWriter), import_writer)
    registry.alias(port(IReadConfigRevisions), revisions)
    registry.factory(forge_offers, lifetime=Lifetime.TENANT)
    registry.service(ConfigAuthoring, lifetime=Lifetime.TENANT)


#: Every key :func:`register` declares, for the store-free graph to declare absent.
_KEYS: tuple[type[Any], ...] = (
    IReadWorkSourceRepository,
    IWriteWorkSourceRepository,
    IReadRepositoryRecordRepository,
    IWriteRepositoryRecordRepository,
    IReadForgeRepository,
    IWriteForgeRepository,
    IReadCredentialRepository,
    IWriteCredentialRepository,
    ISecretCatalog,
    IWriteSecretRepository,
    ISealedSecretRepository,
    ISecretReferences,
    IForgeReferences,
    ICredentialReferences,
    IReadConfigChanges,
    IConfigApplyWriter,
    IConfigImportWriter,
    IReadConfigRevisions,
    IForgeOffers,
    ConfigAuthoring,
)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports and services absent, for the store-free graph."""
    for missing in _KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
