"""The hub settings' registrations: the admin-settings ports, each a member of the borrowed settings graph.

A graph without settings declares each port absent. Every graph holds the borrowed boot ``HubConfig``, the transcript
caps it resolves, and one :class:`HubConfigSource` over it, reading the settings graph's resolution when one is
borrowed."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.observability.transcripts import TranscriptCaps
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


def register_boot(registry: Registry) -> None:
    """Declare the borrowed boot config and the transcript caps it resolves."""
    registry.context(port(HubConfig), lifetime=Lifetime.PROCESS)
    registry.factory(boot_transcript_caps, lifetime=Lifetime.PROCESS)


def register(registry: Registry) -> None:
    """Declare the settings ports and the live config source over the borrowed graph."""
    for build in (settings_admin, settings_reader, settings_authoring, settings_footing):
        registry.factory(build, lifetime=Lifetime.PROCESS)
    registry.factory(settings_config_source, lifetime=Lifetime.PROCESS)


def register_boot_source(registry: Registry) -> None:
    """Declare the live config source over the boot config alone, for a graph with no settings."""
    registry.factory(boot_config_source, lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the settings ports absent, for a graph with no settings."""
    for missing in (IReadHubSettingsAdmin, HubSettingsReader, HubSettingsAuthoring, HubSettingsFooting):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
