"""The hub settings' registrations: the admin-settings ports, each a member of the borrowed settings graph.

A graph without settings declares each port absent. Every graph holds the borrowed boot ``HubConfig``, the transcript
caps it resolves, and one :class:`HubConfigSource` over it, reading the settings graph's resolution when one is
borrowed, the live ``[tracing]`` and ``[egress]`` configs and annotation interval read through that source, the hub's
resolved flags, and the public URL a close note links a chunk's board page under."""

from __future__ import annotations

from blizzard.foundation.flags import Flags, resolve_flags, warn_held_keys, warn_ignored_flags
from blizzard.foundation.logging import get_logger
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.observability.egress.boot import LiveEgressConfig
from blizzard.hub.domain.observability.forge_status import LiveAnnotationInterval
from blizzard.hub.domain.observability.tracing.repository import LiveTracingConfig
from blizzard.hub.domain.observability.transcripts import TranscriptCaps
from blizzard.hub.domain.work_items.closure import BoardPublicUrl
from blizzard.hub.settings.footing import HubSettingsFooting
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.settings.model import IReadHubSettingsAdmin
from blizzard.hub.settings.reader import HubSettingsReader
from blizzard.hub.settings.service import HubSettingsAuthoring
from blizzard.hub.settings.source import HubConfigSource
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def settings_admin(graph: HubSettingsGraph) -> IReadHubSettingsAdmin:
    """The graph's admin store."""
    return graph.store


def settings_reader(graph: HubSettingsGraph) -> HubSettingsReader:
    """The graph's reader."""
    return graph.reader


def settings_authoring(graph: HubSettingsGraph) -> HubSettingsAuthoring:
    """The graph's authoring service."""
    return graph.authoring


def settings_footing(graph: HubSettingsGraph) -> HubSettingsFooting:
    """The graph's footing."""
    return graph.footing


def settings_config_source(boot: HubConfig, graph: HubSettingsGraph) -> HubConfigSource:
    """The live config source over the boot config and the borrowed graph."""
    return HubConfigSource(boot, graph)


def boot_config_source(boot: HubConfig) -> HubConfigSource:
    """The live config source over the boot config alone."""
    return HubConfigSource(boot, None)


def boot_transcript_caps(boot: HubConfig) -> TranscriptCaps:
    """The ceilings the boot config resolves for the transcript ingest lane."""
    return boot.transcripts.transcript_caps()


def live_tracing(source: HubConfigSource) -> LiveTracingConfig:
    """The ``[tracing]`` config read live through ``source``."""
    return LiveTracingConfig(lambda: source.current().tracing)


def live_egress(source: HubConfigSource) -> LiveEgressConfig:
    """The ``[egress]`` config read live through ``source``."""
    return LiveEgressConfig(lambda: source.current().egress)


def live_annotation_interval(source: HubConfigSource) -> LiveAnnotationInterval:
    """The forge-status pass's interval read live through ``source``."""
    return LiveAnnotationInterval(lambda: source.current().annotation_interval_seconds)


def board_public_url(boot: HubConfig) -> BoardPublicUrl:
    """The boot config's public URL, which a close note links each chunk's board page under."""
    return BoardPublicUrl(boot.public_url)


def build_hub_flags(config: HubConfig) -> Flags:
    """The hub's resolved flags: the resolution ``config``'s load made, or — for a config built in code — every ``hub``
    and ``fleet`` flag of the registry it carries at its default, then its ``[flags]`` overrides. Each override the hub
    ignores, and each guarded key read as absent, is warned of here, once per process."""
    resolution = config.flag_resolution or resolve_flags(config.flag_registry, "hub", config.flags)
    log = get_logger("blizzard.hub")
    warn_ignored_flags(log, resolution)
    warn_held_keys(log, config.held_keys)
    return resolution.flags


def register_boot(registry: Registry) -> None:
    """Declare the borrowed boot config, the transcript caps, flags and board public URL it resolves, and the live
    tracing and egress configs and annotation interval."""
    registry.context(port(HubConfig), lifetime=Lifetime.PROCESS)
    registry.factory(build_hub_flags, lifetime=Lifetime.PROCESS)
    registry.factory(boot_transcript_caps, lifetime=Lifetime.PROCESS)
    registry.factory(live_tracing, lifetime=Lifetime.PROCESS)
    registry.factory(live_egress, lifetime=Lifetime.PROCESS)
    registry.factory(live_annotation_interval, lifetime=Lifetime.PROCESS)
    registry.factory(board_public_url, lifetime=Lifetime.PROCESS)


def register(registry: Registry) -> None:
    """Declare the settings ports and the live config source over the borrowed graph."""
    for build in (settings_admin, settings_reader, settings_footing):
        registry.factory(build, lifetime=Lifetime.PROCESS)
    authoring = registry.factory(settings_authoring, lifetime=Lifetime.PROCESS)
    registry.optional(port(HubSettingsAuthoring), authoring, lifetime=Lifetime.PROCESS)
    registry.factory(settings_config_source, lifetime=Lifetime.PROCESS)


def register_boot_source(registry: Registry) -> None:
    """Declare the live config source over the boot config alone, for a graph with no settings."""
    registry.factory(boot_config_source, lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the settings ports absent, for a graph with no settings."""
    for missing in (IReadHubSettingsAdmin, HubSettingsReader, HubSettingsAuthoring, HubSettingsFooting):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    registry.optional(port(HubSettingsAuthoring), None, lifetime=Lifetime.PROCESS)
