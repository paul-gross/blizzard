"""A test root's replacements over the hub's registered graph, through the registry's test override seam.

A root swaps a configured adapter by replacing the one registration that owns its key, so every alias and every bag
field the registration fills resolves to the stand-in, and no production parameter carries it. A replacing factory's
parameters are resolved by their annotations, as the registration's own are."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from blizzard.foundation.registry.interface import Ref
from blizzard.foundation.registry.internal.overrides import OverrideSet
from blizzard.foundation.registry.internal.recording import OWNING, RecordingRegistry
from blizzard.hub.forge.readiness import ICredentialProbe
from blizzard.hub.forge.registry import ForgeRegistry
from blizzard.hub.work_sources.source import IWorkSourceRegistry

#: Builds a tenant's forge registry from the tenant scope's registered collaborators, named by annotation.
type ForgeRegistryBuild = Callable[..., ForgeRegistry]

#: Builds a tenant's work-source registry from the tenant scope's registered collaborators, named by annotation.
type WorkSourceRegistryBuild = Callable[..., IWorkSourceRegistry]


def owned_ref(registry: RecordingRegistry, key: object) -> Ref[Any]:
    """The ``Ref`` of the one registration in ``registry`` that owns ``key``."""
    (token,) = [entry.token for entry in registry.entries if entry.kind in OWNING and entry.key == key]
    return Ref(token)


def replaced_adapters(
    registry: RecordingRegistry,
    *,
    probes: Mapping[str, ICredentialProbe] | None = None,
    forges: ForgeRegistryBuild | None = None,
    work_sources: WorkSourceRegistryBuild | None = None,
) -> OverrideSet:
    """Replacements supplying ``probes`` as every forge kind's credential check, borrowed, and building each tenant's
    forge and work-source registries with ``forges`` and ``work_sources``; each left ``None`` stays registered."""
    overrides = OverrideSet(registry)
    if probes is not None:
        overrides.replace_value(owned_ref(registry, Mapping[str, ICredentialProbe]), probes)
    if forges is not None:
        overrides.replace_factory(owned_ref(registry, ForgeRegistry), forges)
    if work_sources is not None:
        overrides.replace_factory(owned_ref(registry, IWorkSourceRegistry), work_sources)
    return overrides


def replaced_probes(registry: RecordingRegistry, probes: Mapping[str, ICredentialProbe]) -> OverrideSet:
    """Replacements supplying ``probes`` as every forge kind's credential check, borrowed."""
    return replaced_adapters(registry, probes=probes)
