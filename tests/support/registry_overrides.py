"""A test root's replacements over the hub's registered graph, through the registry's test override seam.

A root swaps a configured adapter by replacing the one registration that owns its key, so every alias and every bag
field the registration fills resolves to the stand-in, and no production parameter carries it. A replacing factory's
parameters are resolved by their annotations, as the registration's own are."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from blizzard.foundation.registry.interface import Ref
from blizzard.foundation.registry.internal.overrides import OverrideSet
from blizzard.foundation.registry.internal.recording import OWNING, RecordingRegistry
from blizzard.hub.auth.oauth.registry import IOAuthProviderRegistry
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.delivery.action_pass import ActionPassDriver, PassBound
from blizzard.hub.forge.readiness import ICredentialProbe
from blizzard.hub.forge.registry import ForgeRegistry
from blizzard.hub.settings.source import HubConfigSource
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
    oauth_providers: IOAuthProviderRegistry | None = None,
    action_pass_grace: timedelta | None = None,
) -> OverrideSet:
    """Replacements supplying ``probes`` as every forge kind's credential check, borrowed, and building each tenant's
    forge and work-source registries with ``forges`` and ``work_sources``, and serving ``oauth_providers`` as the
    hub's OAuth provider registry, and letting each action pass go ``action_pass_grace`` past its live bound; each left ``None`` stays registered."""
    overrides = OverrideSet(registry)
    if probes is not None:
        overrides.replace_value(owned_ref(registry, Mapping[str, ICredentialProbe]), probes)
    if forges is not None:
        overrides.replace_factory(owned_ref(registry, ForgeRegistry), forges)
    if work_sources is not None:
        overrides.replace_factory(owned_ref(registry, IWorkSourceRegistry), work_sources)
    if oauth_providers is not None:
        overrides.replace_value(owned_ref(registry, IOAuthProviderRegistry), oauth_providers)
    if action_pass_grace is not None:
        overrides.replace_factory(owned_ref(registry, ActionPassDriver), _action_passes_graced(action_pass_grace))
    return overrides


def _action_passes_graced(grace: timedelta) -> Callable[..., ActionPassDriver]:
    """Builds the action pass driver with ``grace`` past each pass's live bound."""

    def action_pass_driver(loop: ActionLoop, config: HubConfigSource) -> ActionPassDriver:
        return ActionPassDriver(loop, lambda: PassBound(config.current().action_pass_seconds, grace))

    return action_pass_driver
