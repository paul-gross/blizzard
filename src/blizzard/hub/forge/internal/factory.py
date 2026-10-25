"""Builds a repository's forge adapter by its forge's kind, and the per-tenant registry handing out its roles.

A ``kind -> adapter`` map selects the adapter; confined to ``internal/`` (``bzh:dependency-inversion``), so no adapter
class is imported outside here and the composition roots, and ``httpx2`` stays out of the seam."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import httpx2

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.config.secrets import ISecretReader
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.registry import ForgeBinding, ForgeRegistry, IForgeAdapter
from blizzard.hub.live_config import ConfigObjectCache


def build_forge_adapter(
    binding: ForgeBinding,
    *,
    clock: IClock,
    instrument: Callable[[httpx2.AsyncClient], None] | None = None,
) -> IForgeAdapter:
    """The adapter ``binding``'s forge kind names; an unknown kind fails at config load, so it cannot reach here."""
    kinds = {"github": GitHubLanding}
    return kinds[binding.kind](binding, clock=clock, instrument=instrument)


def build_forge_registry(
    *,
    records: IReadRepositoryRecordRepository,
    objects: ConfigObjectCache,
    secrets: ISecretReader,
    clock: IClock,
    capabilities: Mapping[str, frozenset[Capability]] = FORGE_KIND_CAPABILITIES,
    instrument: Callable[[httpx2.AsyncClient], None] | None = None,
) -> ForgeRegistry:
    """A tenant's registry over its repository records, cache, and secret reader."""
    return ForgeRegistry(
        records=records,
        objects=objects,
        secrets=secrets,
        build=lambda binding: build_forge_adapter(binding, clock=clock, instrument=instrument),
        capabilities=capabilities,
    )
